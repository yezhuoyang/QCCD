import QCCDC.Cert.Sound

/-!
# The boards

*Part H, lesson H2. You need lesson H1.*

The site's leaderboards rank hardware designs by how fast, or how reliably, they run one round
of an error-correcting code. Every entry also carries a mark for R10. This lesson asks what that
mark claims, how it was made, and what you can check of it today -- and it checks one entry
completely, in this page.
-/

open QCCDC.Cert

/-!
## 1. What a board says about R10

There are seven boards: BB [[144,12,12]]; the repetition code of distance 9; the five-qubit code
[[5,1,3]]; the Steane code [[7,1,3]]; the distance-3 surface code (17 qubits); and two memory
boards, the repetition code of distance 5 and the surface code of distance 3, ranked by logical
error rate. Each board publishes its rows as `board/<id>/manifest.json`, and each row has a field
`r10`: `true`, `false`, or none. On the entry's card the same field is the row **R10: Lean checker +
tableau**, which reads **passed** when `r10` is `true`, **FAILED** with the checker's reason when it
is `false`, and **not run** when there is none (a design the compiler could not serve).

"passed" claims the whole of R10: the proved checker accepted the certificate (O1), and the
pulses, composed, are the circuit (O2) -- what the semantic check (`check_cert`), run together
with the proved checker, reports as `R10 passed`.

## 2. Where the mark comes from

Every mark is computed. The boards are built by scripts that compile the board's circuit onto
every device, then, for each compiled point, build the checker input (`mk_qcheck_input`) and run
the semantic check (`check_cert`) with the proved checker; `r10` is whether its report says
`R10 passed`. A point the compiler refused has no programme and no mark. (The memory boards'
build computes the mark only on a machine where the checker is built.)

On the BB board that has been so since 2026-10-20. Before that day its entries' marks were typed
by hand in the study's catalogue, `"r10": True` or `"r10": False` beside each design, and nothing
recomputed them when the board was rebuilt. Section 6 shows what that cost.

The scripts that build the boards are not all in the public repository (the BB board's and the
small-code boards' are not); what they did is what the rows record.

## 3. What you can check today

The marks themselves: download a board's `manifest.json` and read `r10` row by row.

The certificates behind them are **not published**: no board page links one, and no file of
one is served. So a board's mark is, today, a claim you cannot re-check from what the site
gives you. There are three ways round that:

* this lesson carries one board certificate in full (section 4);
* every board's circuit ships with the workspace, as its task's `circuit.qasm` (the small-code
  and memory boards' entries were compiled from that text; the BB study's entries from
  ancilla-layout variants of it, which are other files): `qccd boards` lists the boards, and
  `qccd submit --local --board <title>` compiles the board's circuit onto a design of yours and
  grades it, the whole chain, on your own computer (lesson H4);
* submit a design to the official grader, which runs the same chain on your bundle (lesson H3).

## 4. One board certificate, in this page

The repetition-code board's first entry is the round on `stationary_chain`: two traps, `C0` and
`C1`, one rail between them, each trap able to hold 32 ions and gate. The circuit is 17 qubits
(9 data, 8 ancillas): for each ancilla, two CNOTs and a measurement. Below is that entry's
checker input as the board's build made it, as a Lean value -- in the order `Input.ofJson` reads
it, which sorts the qubit map and the starting positions by their keys as text (`"10"` before
`"2"`). The board's row matches it: 95 instructions after cooling, round time 2.91 ms, `r10`
true. The board's own studio page plays this programme:
[the repetition board's stationary_chain entry](../board/rep9/01_stationary_chain.html).
-/

def rep9Chain : Input :=
  { circuit := "rep9_esm", arch := "stationary_chain", nQubits := 17,
    qmap := [(0, "q0"), (1, "q1"), (10, "q10"), (11, "q11"), (12, "q12"), (13, "q13"), (14, "q14"), (15, "q15"), (16, "q16"), (2, "q2"), (3, "q3"), (4, "q4"), (5, "q5"), (6, "q6"), (7, "q7"), (8, "q8"), (9, "q9")],
    init := [("q0", "C1"), ("q1", "C0"), ("q10", "C0"), ("q11", "C0"), ("q12", "C0"), ("q13", "C0"), ("q14", "C0"), ("q15", "C0"), ("q16", "C0"), ("q2", "C0"), ("q3", "C0"), ("q4", "C0"), ("q5", "C0"), ("q6", "C0"), ("q7", "C0"), ("q8", "C0"), ("q9", "C1")],
    ops := [⟨0, "cx", [0, 9]⟩, ⟨1, "cx", [1, 9]⟩, ⟨2, "measure", [9]⟩, ⟨3, "cx", [1, 10]⟩, ⟨4, "cx", [2, 10]⟩, ⟨5, "measure", [10]⟩, ⟨6, "cx", [2, 11]⟩, ⟨7, "cx", [3, 11]⟩, ⟨8, "measure", [11]⟩, ⟨9, "cx", [3, 12]⟩, ⟨10, "cx", [4, 12]⟩, ⟨11, "measure", [12]⟩, ⟨12, "cx", [4, 13]⟩, ⟨13, "cx", [5, 13]⟩, ⟨14, "measure", [13]⟩, ⟨15, "cx", [5, 14]⟩, ⟨16, "cx", [6, 14]⟩, ⟨17, "measure", [14]⟩, ⟨18, "cx", [6, 15]⟩, ⟨19, "cx", [7, 15]⟩, ⟨20, "measure", [15]⟩, ⟨21, "cx", [7, 16]⟩, ⟨22, "cx", [8, 16]⟩, ⟨23, "measure", [16]⟩],
    moves := [⟨6, "q1", "C0", "C1"⟩, ⟨12, "q1", "C1", "C0"⟩],
    rots := [],
    gates := [⟨0, 5, "C1", ["q0", "q9"]⟩, ⟨1, 11, "C1", ["q1", "q9"]⟩, ⟨3, 17, "C0", ["q1", "q10"]⟩, ⟨4, 23, "C0", ["q2", "q10"]⟩, ⟨6, 28, "C0", ["q2", "q11"]⟩, ⟨7, 34, "C0", ["q3", "q11"]⟩, ⟨9, 39, "C0", ["q3", "q12"]⟩, ⟨10, 45, "C0", ["q4", "q12"]⟩, ⟨12, 50, "C0", ["q4", "q13"]⟩, ⟨13, 56, "C0", ["q5", "q13"]⟩, ⟨15, 61, "C0", ["q5", "q14"]⟩, ⟨16, 67, "C0", ["q6", "q14"]⟩, ⟨18, 72, "C0", ["q6", "q15"]⟩, ⟨19, 78, "C0", ["q7", "q15"]⟩, ⟨21, 83, "C0", ["q7", "q16"]⟩, ⟨22, 89, "C0", ["q8", "q16"]⟩],
    unrealised := [],
    gateSites := ["C0", "C1"],
    hops := [("C0", "C1"), ("C1", "C0")],
    loops := [] }

/-!
This device has no studio page in the tutorial, so there is no figure of it here; the
certificate is short enough to read. Two ions start in `C1` (`q0` and the first ancilla `q9`),
the other fifteen in `C0`. The first ancilla's two CNOTs happen in `C1`, every other gate in
`C0`. The one ion that moves is `q1`: it goes to `C1` for the first ancilla's second CNOT and
comes back.

Run through `qcheck`, this input prints `24 circuit ops, 16 gate witnesses, 2 moves, 0 rotations,
17 qubits` and `ACCEPTED`. Both its circuit (`rep9_esm.qasm`, the board's task circuit) and its
device (`stationary_chain.arch.json`) are in the repository, and when this lesson was written,
compiling the one onto the other with the published compiler gave a checker input identical to
this one.

#### Exercise 1 (★) `rep9_counts`
Prove the value has those counts.
-/

theorem rep9_counts :
    rep9Chain.ops.length = 24 ∧ rep9Chain.gates.length = 16 ∧ rep9Chain.moves.length = 2 ∧
      rep9Chain.rots.length = 0 ∧ rep9Chain.nQubits = 17 :=
  ⟨rfl, rfl, rfl, rfl, rfl⟩

/-!
#### Exercise 2 (★) `rep9_holds`
Prove that the board entry's certificate holds -- the O1 half of its R10 mark, decided by the
kernel in your browser.
-/

theorem rep9_holds : Implements rep9Chain :=
  check_sound (by decide +kernel)

/-!
#### Exercise 3 (★★) `rep9_first_visit`
Find the cycle at which the witness for op 1 (`cx q[1],q[9]`) fires, in trap `C1`, and prove
that both its ions are there at that cycle. Replace `sorry` by `⟨t, p1, p2, p3⟩`: membership in
the literal list by `simp [rep9Chain]` (as in lesson F1), the two positions by computation.
-/

theorem rep9_first_visit :
    ∃ t, (⟨1, t, "C1", ["q1", "q9"]⟩ : GateW) ∈ rep9Chain.gates ∧
      look (posAt rep9Chain t) "q1" = some "C1" ∧ look (posAt rep9Chain t) "q9" = some "C1" :=
  ⟨11, by simp [rep9Chain], by decide +kernel, by decide +kernel⟩

/-!
#### Exercise 4 (★★) `rep9_readouts_unwitnessed`
The eight measurements have no witness. Prove it: no witness names an op for which
`needsWitness` is `false`.
-/

theorem rep9_readouts_unwitnessed :
    ∀ o ∈ rep9Chain.ops, needsWitness o = false → ∀ g ∈ rep9Chain.gates, g.dag ≠ o.idx := by
  decide +kernel

/-!
## 5. What a mark gives you, gate by gate

"R10 passed" is a statement about the whole round. What does it say about one CNOT? Every op
that needs a witness has exactly one (`Covered`), and every witness's ions are together, at its
cycle, in a trap that can gate (`GatesLegal`). Put the two together and you get, for each gate of
the round, a meeting.

#### Exercise 5 (★★★) `holds_gives_meeting`
Prove it for every certificate. From `Covered`, the list of witnesses for `o` has length 1, so it
is not empty: `cases hf : inp.gates.filter (fun g => g.dag = o.idx)` gives you its first element.
`List.mem_filter` says what membership in a filtered list means. The conditions are
`h.2.2.2.2.2.2.1` (`GatesLegal`) and `h.2.2.2.2.2.2.2.2.1` (`Covered`).
-/

theorem holds_gives_meeting {inp : Input} (h : Implements inp) {o : Op} (ho : o ∈ inp.ops)
    (hn : needsWitness o = true) :
    ∃ g ∈ inp.gates, g.dag = o.idx ∧ g.site ∈ inp.gateSites ∧
      ∀ i ∈ g.ions, look (posAt inp g.t) i = some g.site := by
  have hc := h.2.2.2.2.2.2.2.2.1 o ho hn
  obtain ⟨g, hg⟩ : ∃ g, g ∈ inp.gates.filter (fun g => g.dag = o.idx) := by
    cases hf : inp.gates.filter (fun g => g.dag = o.idx) with
    | nil => rw [hf] at hc; simp at hc
    | cons g _ => exact ⟨g, by simp⟩
  rw [List.mem_filter] at hg
  have hl := h.2.2.2.2.2.2.1 g hg.1
  exact ⟨g, hg.1, by simpa using hg.2, hl.1, hl.2⟩

/-!
#### Exercise 6 (★★) `rep9_every_cx_meets`
Apply it to the board entry: every CNOT of the round has a witness whose ions meet in a trap
that can gate.
-/

theorem rep9_every_cx_meets :
    ∀ o ∈ rep9Chain.ops, needsWitness o = true →
      ∃ g ∈ rep9Chain.gates, g.dag = o.idx ∧ g.site ∈ rep9Chain.gateSites ∧
        ∀ i ∈ g.ions, look (posAt rep9Chain g.t) i = some g.site :=
  fun _ ho hn => holds_gives_meeting rep9_holds ho hn

/-!
## 6. Why a typed mark can go stale

On 2026-10-19 the order condition became strict (lesson F1, condition 11): two ops sharing a
qubit must be witnessed in circuit order **even when they commute**. Two CNOTs with a common
control commute, so swapping them changes no unitary, and O2 cannot see the swap; only the order
checks catch it -- condition 11 on the certificate, and the semantic check's order check, made
strict the same day, on the programme. A certificate written before that day could have such a swap,
be accepted then, and be refused now.

Here is the smallest such case, on `chain4`. One control ion `a` serves two targets, `b` in `C0`
and `c` in `C2`. The circuit says `cx a,b` first. The certificate serves `c` first, because `a`
went that way first. Every move is a hop, every witness finds its ions together:
-/

def fanReordered : Input :=
  { circuit := "fan", arch := "chain4", nQubits := 3,
    qmap := [(0, "a"), (1, "b"), (2, "c")],
    init := [("a", "C1"), ("b", "C0"), ("c", "C2")],
    ops := [⟨0, "cx", [0, 1]⟩, ⟨1, "cx", [0, 2]⟩],
    moves := [⟨1, "a", "C1", "C2"⟩, ⟨4, "a", "C2", "C1"⟩, ⟨5, "a", "C1", "C0"⟩],
    rots := [],
    gates := [⟨0, 7, "C0", ["a", "b"]⟩, ⟨1, 3, "C2", ["a", "c"]⟩],
    unrealised := [],
    gateSites := ["C0", "C1", "C2", "C3"],
    hops := [("C0", "C1"), ("C1", "C0"), ("C1", "C2"), ("C2", "C1"), ("C2", "C3"), ("C3", "C2")],
    loops := [] }

/-!
{{figure fanReordered at=3}}

Every row under the figure is green, and the checker still says no:
-/

#eval check fanReordered
#eval diagnose fanReordered

/-!
#### Exercise 7 (★★) `fan_reordered_rejected`
Prove the refusal with `rejects_reordering` (lesson F1, section 6). It wants two members of
`witnessQubits fanReordered` -- each witness joined with its op's qubits, `⟨op, cycle, qubits⟩`
-- the earlier op first, sharing a qubit, and the earlier op's cycle not before the later's.
-/

theorem fan_reordered_rejected : check fanReordered = false :=
  rejects_reordering (a := ⟨0, 7, [0, 1]⟩) (b := ⟨1, 3, [0, 2]⟩)
    (by decide +kernel) (by decide +kernel) (by decide) (by decide) (by decide)

/-!
The BB board is where this happened. Until 2026-10-20 its marks were typed, so here is what they
said that day, beside what the semantic check, with the proved checker, said when re-run on each
compiled entry's own certificate, circuit and device. That day the board had 21 entries; 6 were
devices the compiler could not serve, with no programme and no mark. The other 15:
-/

/-- One compiled entry of the BB board: its mark, and whether R10 passed when re-checked on
2026-10-20. -/
structure BBEntry where
  key : String
  marked : Bool
  passesToday : Bool
  deriving Repr, DecidableEq

def bbBoard : List BBEntry :=
  [⟨"01_baseline_stride", true, false⟩, ⟨"02_shipped_ring_code_aware", true, true⟩,
   ⟨"03_h3_v24_192", false, true⟩, ⟨"04_h4_v24_216", false, true⟩,
   ⟨"05_h3_v36_288_ANSWER", true, true⟩, ⟨"06_h5_v36_360", false, true⟩,
   ⟨"07_h3_v48_384", false, true⟩, ⟨"08_h4_v72_360_unbuildable", false, true⟩,
   ⟨"09_grid13x13", false, true⟩, ⟨"10_grid11x11", false, true⟩,
   ⟨"11_grid11x11_s2_buildable", false, true⟩, ⟨"12_grid13x13_broadcast", false, true⟩,
   ⟨"22_planar12_own_a72", false, true⟩, ⟨"23_planar12_own_a24", false, true⟩,
   ⟨"24_cylinder12_own_a72", false, true⟩]

/-!
Three entries were marked passed. The first, the stride layout, was compiled on 2026-09-05; its
certificate is now refused, by the proved checker and by the semantic check's own order check. The
checker's first reason:

```
two ops sharing a qubit are out of order: op 92 (cx [153, 10]) comes before op 93
(cx [153, 11]) in the circuit, but its witness is at cycle 45, not before 37
```

-- two CNOTs sharing the control `q[153]`, exactly the swap of the figure above. The twelve
entries marked "not run" all passed. A computed mark would have said both things, and since that
day the board's marks are computed. The stride layout was then compiled again, with the published
compiler, which keeps every qubit's operations in circuit order: its certificate passes R10, and
its round takes 1,419 ms instead of 522 ms, in 824 transport batches instead of 546. Serving the
CNOTs in the order the circuit gives them costs this layout time; the old certificate's speed
came partly from breaking that order.

#### Exercise 8 (★) `a_stale_mark`
Find the entry whose typed mark said passed and whose certificate no longer does.
-/

theorem a_stale_mark : ∃ e ∈ bbBoard, e.marked = true ∧ e.passesToday = false :=
  ⟨⟨"01_baseline_stride", true, false⟩, by decide +kernel, rfl, rfl⟩

/-!
## 7. Why the boards' certificates are not in this page

The kernel can prove a certificate of tens of events while you wait; the boards' certificates
run from that to thousands. Measured on the checker inputs behind the boards (for BB, whose
build makes none, built from each entry's certificate and device with `mk_qcheck_input`):

| entry | checker input | ops | witnesses | moves | rotations |
|---|---|---|---|---|---|
| repetition, `stationary_chain` (above) | 5.5 KB | 24 | 16 | 2 | 0 |
| Steane, `stationary_chain` | 9.9 KB | 36 | 30 | 24 | 0 |
| repetition memory, `ring16` | 19 KB | 76 | 40 | 80 | 39 |
| Steane, `ring144_24v` | 65 KB | 36 | 30 | 582 | 0 |
| BB, `ring144_24v` code-aware layout | 333 KB | 1296 | 1008 | 1728 | 54 |
| BB, ring(143, 3, 36), the study's answer | 463 KB | 1296 | 1008 | 3312 | 46 |

On the machine this lesson was written on, proving the Steane round on `ring144_24v` by
`decide +kernel` took Lean about 30 s natively, and the compiled `qcheck` decided each BB round
above in under half a second. A BB certificate is for `qcheck`, not for the kernel: the same
`check`, compiled, and trusted because `check_sound` is proved about it.

So the gap is not the checker. It is that the files are not published; the
repetition-code entry above is the one board certificate you can check without compiling
anything yourself.
-/
