# The QCCD compiler — QASM in, hardware instructions out

Status: plan v1 · 2026-08-23 · scope `Compiler/`
Reads on: [`docs/PLAN.md`](../docs/PLAN.md) §7 (the pipeline this implements), [`docs/tsir.md`](../docs/tsir.md)
(the output format), [`docs/rules.md`](../docs/rules.md) (the acceptance criteria).

---

## 0. What already exists, and what this must therefore not rebuild

The platform is not a blank page. Before designing anything, here is what is already load-bearing
and will be *consumed*, not reimplemented:

| layer | where | status |
|---|---|---|
| architecture graph + primitive curves | `qccd/arch/` | done, 9 devices, expands to explicit nodes/segments/loops |
| **TSIR — the hardware instruction format** | `qccd/ir/tsir.py` | done, 7 instruction types, JSON round-trip |
| replay + the rule set (23 when this was written; 25 today) | `qccd/verify/` | done, and adversarially reviewed |
| cost models (combinatorial, µs, quanta) | `qccd/cost/` | done, two tables |
| a compiler | `qccd/compile/pipeline.py` | **BB-code-only, closed-loop-only**, 7 passes |

So the target instruction set is **already defined and already evaluable**. A compiled program is a
`.tsir.json`; `qccd.verify.verify(prog, arch, model)` replays it, checks 23 hardware rules and
prices it. That is the "Program that can be accepted by the Hardware and being evaluated" from the
brief, and it exists.

The existing `qccd/compile/pipeline.py` is a *specialised* compiler: its input is a `BBCode` object,
not a circuit; it requires a closed rotation loop (`raise ValueError` if the device has none); its
router is one monotone offset sweep; and it emits `gate: "CX"` as an opaque token with no
decomposition. It is excellent as an **oracle** and useless as a general compiler.

The gap this project fills, precisely:

1. **any QASM program** — not a stabilizer code object;
2. **any architecture** — grid, ladder, racetrack, chain, single trap, not only a ring;
3. **gate decomposition** — `CX` → a native trapped-ion pulse sequence, which nothing currently does;
4. **optimal routing** — SAT + numerical optimization, where today there is one greedy sweep;
5. **verified** — because right now nothing checks that the compiled program means the same thing
   as the circuit.

Point 5 is not an aspiration bolted on for rigour. It is a hole the platform has already named.

---

## 1. The deliverable, stated as a rule the platform already wrote

`qccd/verify/__init__.py:55`:

```python
UNCHECKABLE: Mapping[str, str] = {
    "R7b": "no per-zone duty-cycle budget is declared by any architecture yet",
    "R10": "needs symbolic permutation + Pauli-frame tracking against a QASM DAG",
}
```

**R10 — "the compiled program implements the input circuit" — is the one rule in the system that no
amount of replaying can settle.** It is reported `skipped`, never `passed`, and it is skipped for
exactly one reason: there is no QASM DAG to check against, because there is no QASM front end.

That makes the acceptance criterion for this whole project unusually clean, and it was written by
someone other than me:

> **The compiler is done when `verify()` reports R10 as `passed` on a compiled program, and the
> evidence it passes on is checked by something small enough to trust.**

Everything below is in service of that sentence. It also fixes the shape of the output: the compiler
must emit not only a TSIR program but a **certificate** that R10 can be discharged against.

### R10, factored into three obligations

"The compiled program implements the input circuit" is too big to prove in one step. It factors:

| | obligation | nature | discharged by |
|---|---|---|---|
| **O1** | every 2Q gate of the circuit DAG is realized by exactly one TSIR `gate` whose two ions are co-located at a gate-capable site at that instant, and DAG dependency order is respected | combinatorial | verified checker over the routing certificate |
| **O2** | each logical gate is replaced by a native pulse sequence whose unitary equals it up to global phase | algebraic, finite | a fixed table of Lean theorems, proved once |
| **O3** | virtual-Z / Pauli-frame commutation and transport-induced relabeling are tracked correctly | symbolic | frame checker, folded into O1's certificate |

`O1 ∧ O2 ∧ O3 ⇒ R10`. The three are independently checkable, which is the point: O2 is a bounded
one-time algebra job, O1/O3 are per-program and must be cheap.

O3 is where compiler bugs actually live. A router that moves ion `d17` through a `sort_merge` has
permuted two logical qubits and every later gate on them is now on the wrong operands. Nothing in
the current stack would notice.

---

## 2. Trust architecture: search is untrusted, checking is trusted

This mirrors the discipline already used in `LeanQEC/verifier-ml` — an untrusted OCaml producer, a
pinned trusted checker surface (`RULES.lock`, `TCB.md`), and proof-carrying certificates. Do not
invent a second methodology.

```
     ┌─────────────────────── UNTRUSTED ───────────────────────┐
     │  SAT/SMT solver (z3, pysat)   annealing   λ-optimizer    │
     │  may be arbitrarily clever, arbitrarily buggy            │
     └──────────────────────────┬──────────────────────────────┘
                                │ emits
                    program.tsir.json  +  program.qcert.json
                                │
     ┌──────────────────────────┴──────────────────────────────┐
     │  TRUSTED CHECKER                                         │
     │  Lean  QCCDC.Cert.Check  (executable, proved sound)      │  ← the TCB
     │  OCaml Check.checker     (fast pre-flight, differential) │
     └──────────────────────────┬──────────────────────────────┘
                                │ verdict
                    qccd.verify.verify(...)  →  R10: passed
```

Three consequences worth stating plainly:

- **The SAT solver is never trusted.** A solver returning a wrong model produces a certificate the
  checker rejects. This is what lets me use z3 through a Python bridge without that Python code
  entering the trusted base.
- **The checker recomputes; it never reads.** Ion positions are derived by replaying the move list
  from the initial placement — the certificate's *claimed* positions are cross-checked, not
  believed. This is exactly the discipline `qccd/ir/import_deck.py` already applies to the shipped
  artifact ("It does **not** trust the artifact"), and for the same reason: totals can agree by
  coincidence, recomputed positions cannot.
- **The checker is small on purpose.** If O1's checker grows past ~500 lines it has stopped being a
  checker and become a second compiler. Anything that tempts it to grow belongs in the certificate
  instead, as a witness the checker merely validates.

---

## 3. Language split, and why each is where it is

| part | language | why |
|---|---|---|
| compiler proper | **OCaml 5.3.0** | as requested. Sum types + exhaustive matching make an IR-to-IR pass hard to get subtly wrong; no GC pauses in a solver loop; the user already runs an OCaml verifier next door |
| semantics + checker soundness | **Lean 4.29.0-rc2 + Mathlib** | as requested. Concrete 4×4 complex matrices for O2, pure combinatorics for O1/O3 |
| SAT/SMT oracle | **Python bridge → z3 4.16.0 / pysat** | untrusted; no OCaml SAT binding is installed and installing one buys nothing when the result is checked anyway |
| evaluation, rules, cost, viz | **Python (existing)** | done, adversarially reviewed, and the differential oracle |

**Toolchain verified on this machine, not assumed** (this is why the plan opens here rather than
discovering it in week three):

```
OCaml   5.3.0     opam switch `default`   →  dune 3.19.0, yojson 2.2.2, zarith 1.14
                  NOTE: PATH needs $HOME/AppData/Local/opam/default/bin prepended;
                  `ocaml` is not on PATH by default (same trap verifier-ml/ocamlenv.sh solves)
                  a hello-world dune+yojson build and run was confirmed
Lean    4.29.0-rc2 with Mathlib PREBUILT (7,676 .olean) in LeanQEC/.lake/packages
                  → pin the same toolchain + mathlib rev 3542f17d to reuse that cache.
                  Getting this wrong costs a multi-hour Mathlib rebuild.
SAT     z3 4.16.0 (python module only, no binary), python-sat — both import cleanly
Python  3.14.3, qiskit 2.3.1, stim — the differential oracles for the front end
```

**The rule that keeps this from becoming a three-language swamp:** JSON is the only interface. OCaml
reads *expanded* `.arch.json` (`Architecture.to_json(expanded=True)` already emits explicit
nodes/segments/loops, so the generators are never reimplemented) and writes `.tsir.json` +
`.qcert.json`. Nothing is shared but documents on disk. Every stage is independently runnable and
independently diffable.

---

## 4. The pipeline

```
   .qasm ──┐
           ├──▶ 1 parse ──▶ 2 DAG ──▶ 3 decompose ──▶ 4 place ──▶ 5 route ──▶ 6 aggregate
 .arch.json┘        │          │           │              │           │            │
                    │          │      (Lean table)   (numerical)    (SAT)      (R4/R4b/R4d)
                    │          │                                                    │
                    └──────────┴─────────────────────────────▶ 7 opoint ──▶ 8 cool ─┤
                                                                                    ▼
                                                        program.tsir.json + program.qcert.json
                                                                                    │
                                              Lean qcheck ──▶ O1∧O2∧O3 ──▶ qccd.verify ──▶ R10 ✓
```

**1 parse.** OpenQASM 2.0 plus the QASM 3 subset qiskit emits. Hand-written lexer + recursive
descent — a grammar this small does not need menhir, and a hand-written parser gives better error
positions, which matters when the input is machine-generated.

**2 DAG.** Gates as nodes, qubit/clbit dependency edges. Commutation rules so the scheduler has
freedom (two CZs on a shared control commute; that is real slack the router can spend).

**3 decompose.** Each gate → native `{MS(θ), R(θ,φ), RZ(θ)}` via a table whose entries are
*theorems*, not code. See §8.

**4 place.** Logical qubit → ion → initial site. Spectral seed + annealing; the numerical half. §7.

**5 route.** The hard part. SAT over a time-expanded graph. §6.

**6 aggregate.** Pack moves into SIMD cycles legal under R4 (≤ `max_simd_classes_per_cycle` classes
per cycle — **1** on the shipped ring), R4b (never mix intra with inter), R4d (drivable by the
declared control channels).

**7 opoint.** Choose a point on each primitive's `(µs, quanta)` curve. §7.

**8 cool.** Insert cooling to satisfy R7/R7c. `qccd/compile/cooling.py` already does this correctly
and provably converges in one pass — **call it, do not rewrite it.**

---

## 5. The interface contract

Two documents, both versioned, both round-trippable.

**Input** — expanded architecture, produced by the existing Python:

```bash
python -m qccd export arch/ring144_24v.arch.json -o build/ring144_24v.expanded.json
```

**Output** — the TSIR program (format already fixed by `docs/tsir.md`) plus the certificate:

```jsonc
{ "version": 1,
  "circuit_sha256": "…",              // binds the certificate to one QASM file
  "arch_sha256": "…",                 // …and to one architecture
  "map":    { "q0": "d0", "q1": "d1" },          // logical qubit -> ion  (O1)
  "init":   { "d0": "S0",  "d1": "S1"  },        // ion -> initial site
  "moves":  [ {"t": 3, "ion": "d17", "from": "S17", "to": "S18", "cls": "rotate_cw"} ],
  "gates":  [ {"dag": 41, "t": 7, "site": "A18", "ions": ["d17","a18"],
               "pulses": ["R(pi/2,pi/2)@d17", "MS(pi/4)@d17,a18", "..."] } ],
  "frame":  [ {"t": 12, "kind": "swap", "ions": ["d3","d4"]} ],   // O3
  "claims": { "makespan_us": 61840.0, "peak_quanta": 8.4 } }
```

The checker's contract, stated as the theorem it must satisfy:

```
check(circuit, arch, tsir, cert) = true
  →  ∀ gate g in circuit.dag,
       ∃! instr in tsir with instr.id = cert.gates[g].t,
         positions_replayed_from(cert.init, cert.moves) at that instant
           puts cert.map[g.operands] co-located at a gate-capable site,
       ∧ dag order respected,
       ∧ pulses[g] denotes g up to global phase        (O2, discharged by table)
       ∧ frame is consistent                            (O3)
```

`claims` are claims, never inputs — the same separation `docs/tsir.md` already draws between
annotations and content, and the same reason R9 exists.

---

## 6. Routing — the hard part

The brief calls routing the most difficult part. It is, and for a reason specific to this hardware
that is worth naming because it is what makes off-the-shelf multi-agent-pathfinding solvers
inapplicable.

### Why this is not MAPF

In ordinary MAPF each agent picks its own move. Here, **one broadcast waveform drives every site it
reaches, so every ion moving in a cycle must be executing the *same* class.** `ring144_24v` declares
`max_simd_classes_per_cycle: 1` over 7 classes (`rotate_cw`, `rotate_ccw`, `dock`, `undock`,
`sort_merge`, `sort_split`, `shuttle`). You cannot move ion A left and ion B right in one cycle. You
often cannot move A without also moving every other ion on the same rail.

This is R4, and the README states its consequence bluntly: *"one broadcast waveform makes every site
it drives do the same thing — so 'move these two ions past each other' may not be a step the
hardware can take at all."*

A greedy router cannot see this. It is a global constraint coupling all agents at every timestep —
which is precisely the shape SAT is good at.

### Three regimes, not one problem — measured, not assumed

The constraint does *not* bite equally on every device, and the discriminator is not the geometry.
R4d is enforced through `path_actions`, which considers only moves whose segment belongs to a
**named loop** — "a named path is one conveyor, so forward one slot is the same instruction to every
site on it." A move off a named path has no conveyor, no direction and no verdict. And `direct`
channel grouping is one channel per site, so a channel never drives two sites and R4d's
"asked to do two different things" can never fire.

Measured across the shipped devices (`grouping` and `n_channels` from `ControlPlane`, loop
membership counted over `Device.segments`):

| device | grouping | channels | segments | on a named loop | regime |
|---|---|---:|---:|---:|---|
| `ring144_24v` | broadcast | 46 | 168 | **144** | conveyor |
| `ladder_2x72` | broadcast | 56 | 320 | **284** | conveyor |
| `cyclone_base` | broadcast | 38 | 72 | **72** | conveyor |
| `deck_unit_cell` | broadcast | **44** | 288 | **0** | broadcast-free |
| `grid9x9` | direct | 5,760 | 288 | 0 | free |
| `chain72` | direct | 0 | 71 | 71 | free |
| `stationary_chain` | direct | 0 | 1 | 1 | free |

- **conveyor** — named loops under broadcast wiring. Every ion moving along a loop in a cycle moves
  the same signed delta. The hard case, and the one no MAPF solver expresses.
- **free** — direct wiring. R4d is vacuous; only R4's class budget and the occupancy rules bind.
- **broadcast-free** — the interesting one. See below.

`Arch.regime` computes this, and the OCaml CLI reports it per device.

### A soundness obligation the verifier does not impose

`deck_unit_cell` drives 225 nodes from **44 broadcast channels** and has **zero** segments on a
named loop. R4d therefore has nothing to judge there: a router may emit any move pattern it likes
and the verifier will not object, because R4d reports *not judged* rather than *pass* — which is
honest, and is exactly what `qccd/verify/control.py` documents ("silence there is not a pass").

But a compiler that treats silence as permission emits schedules 44 DACs could never drive. This is
the README's own showcase — `grid9x9` and `deck_unit_cell` are the *same* 225-node lattice and
differ only in wiring — so getting it wrong would corrupt precisely the comparison the platform
exists to make.

**So the router imposes channel drivability itself on broadcast-free devices**, rather than
inheriting it from R4d. Concretely: constraint 3 in the encoding below is generated against the
declared `ControlPlane` groups, not against loop membership. Where the compiler is stricter than the
verifier it says so in the pass report, because a constraint nobody can see is a constraint nobody
can check.

(Two smaller observations from the same measurement, noted rather than acted on: `chain72` and
`stationary_chain` report **0 channels** under `direct` grouping, which cannot drive anything and
looks like a declaration gap in those two architecture documents; and `deck_unit_cell` has no
site-to-site segment at all, since every trap on a grid is separated from its neighbour by a
junction.)

### The encoding

Bounded horizon `T`, over the expanded graph.

*Variables* — pruned by reachability (ion `i` can only be at nodes within `t` hops of its start, and
within `T−t` hops of a node it must reach; this typically cuts 90%+ of the naive product):

| | meaning |
|---|---|
| `x[i,v,t]` | ion `i` occupies node `v` at cycle `t` |
| `k[c,t]` | SIMD class `c` is active at cycle `t` |
| `g[e,t]` | DAG gate `e` executes at cycle `t` |

*Constraints* — each maps to a rule the verifier will independently re-check, which is the whole
point of encoding them rather than hoping:

```
 1. exactly-one   Σ_v x[i,v,t] = 1                              (ladder encoding)
 2. transition    x[i,v,t] ∧ x[i,w,t+1] → segment(v,w) ∨ v = w
 3. LICENSING     x[i,v,t] ∧ x[i,w,t+1] ∧ v≠w → k[class(v→w), t]        ← R4, the crux
 4. class budget  Σ_c k[c,t] ≤ max_simd_classes_per_cycle               ← R4
 5. capacity      Σ_i x[i,v,t] ≤ cap(v)                          (totalizer)   R1
 6. junction      Σ_i x[i,j,t] ≤ 1  for deg(j) ≥ 3                            R2
 7. segment       Σ moves on s at t ≤ s.capacity                              R3
 8. no head-on    ¬( move(i, v→w, t) ∧ move(j, w→v, t) )                      R5
 9. meeting       g[e,t] → ⋁_{v gate-capable} ( x[i_e,v,t] ∧ x[j_e,v,t] )     R6b
10. once          Σ_t g[e,t] = 1
11. dag order     g[e,t] → Σ_{t'<t} g[e',t'] = 1   for each e' ≺ e
12. no mixing     g[e,t] → ¬k[c,t]                                            R4b
```

Constraint 3 is the one that does not appear in any MAPF encoding and is the reason this is a SAT
problem rather than a flow problem.

*Objective.* Minimise `T` by incremental solving under assumption literals — solve at `T`, then
assert `T−1`; an UNSAT at `T−1` is a **proof of makespan optimality**, which is the deliverable that
makes SAT worth the cost. Secondary objective (total moves, hence total heating) via PB constraints
or MaxSAT.

*Symmetry breaking.* Ancillas serving the same stabilizer are interchangeable; add lex-leader
constraints over their index vectors. Without this the solver re-derives `k!` equivalent schedules.

### Being honest about scale

144 ions × 168 nodes × 200 cycles ≈ 4.8M position variables. **Monolithic SAT will not do that**, and
a plan that pretends otherwise is a plan that fails in month four. Three tiers, all emitting the
*same* certificate format and all checked by the *same* checker:

| tier | instance | method | what it is for |
|---|---|---|---|
| **exact** | ≤ 20 ions, ≤ 40 nodes, `T` ≤ 30 | monolithic SAT, optimality proved | **the optimality oracle** — measures how far the heuristic is from optimal |
| **windowed** | full device, horizon split at sync points | SAT per window, certificates concatenate | the production path where it is affordable |
| **heuristic** | full scale | token-swap on loops / A* on the time-expanded graph, class-aware | always available; correctness comes from the checker, not from the search |

This tiering is not a compromise, it is the scientifically useful structure and it is what
`docs/PLAN.md` §7 already asks for ("SAT/ILP on small instances as an optimality oracle"). **A
heuristic with no optimality oracle produces unfalsifiable numbers** — the same trap M1 and M3 exist
to avoid.

The headline number this produces: *the class-broadcast constraint costs X× makespan versus
unconstrained MAPF on the same graph.* Nobody has measured that, and it is directly the thesis
question in `docs/PLAN.md` §1.

---

## 7. Where the numerical optimization actually lives

A finding from reading `qccd/arch/curves.py` before designing this, which changes what to build:

**The operating-point curves are currently 1–4 points, and most points are different *tables*, not
different operating points.** `shuttle_segment` has a genuine trade-off only within
`transport_excitation` — 12 µs @ 1.0 quanta versus 14 µs @ 0.1 quanta. So "pick a point on the
curve" is today a choice between two options, not a continuous optimization. Building a continuous
optimizer for it would be building machinery with nothing to optimize.

The real numerical problems, in order of payoff:

**(a) Placement — spectral relaxation + annealing.** Minimizing total routing distance over an
initial qubit→site map is a quadratic assignment problem. The continuous relaxation — Fiedler
vector of the interaction-graph Laplacian, projected onto the device's node ordering — is a genuinely
good seed and is cheap. Anneal from there. On a ring this is circular arrangement, where the
spectral seed is close to optimal; `qccd/compile/place.py` already anneals but seeds from identity
or interleaved, so this is a measurable improvement with an existing baseline to beat.

**(b) The λ trade-off — makespan against heating.** This is the coupling that matters and it is
continuous even though the choices are discrete. Minimize `Σ_gates ε(n̄) + λ · makespan`; each
primitive instance's choice is then independent given λ, so a **bisection on λ** traces the entire
Pareto frontier in a few dozen solves. It also sets cooling frequency, because a cool costs makespan
and buys n̄ — the same exchange rate. Output: a (runtime, total quanta) Pareto curve, which is
`docs/PLAN.md` M5's deliverable and currently produced by nothing.

**(c) Event scheduling — an LP.** Given a fixed instruction order, choosing start times to minimize
makespan subject to precedence and resource-disjointness is a linear program. `qccd/compile/schedule.py`
currently does earliest-feasible greedy. LP gives the true optimum and, more usefully, the *dual*
— which tells you which resource is actually binding, i.e. which piece of hardware to buy more of.
That is the architecture question the whole platform exists to ask.

So: **SAT owns the discrete routing; numerical optimization owns placement, the heating/time
exchange rate, and the schedule LP.** Not "SAT plus a continuous optimizer for operating points,"
which the data does not currently support.

---

## 8. The Lean development

Pinned to `leanprover/lean4:v4.29.0-rc2` and Mathlib `3542f17d` to reuse the prebuilt cache next
door. Three files, three jobs.

### O2 — pulse-sequence correctness (`QCCDC/Pulse/`) — **done**

Two identities carry the whole story, and both are proved:

```
u3 θ φ λ = VZ (φ+λ) · R (θ, π/2 − λ)                          QCCDC.u3_decomp
CX = e^{-iπ/4} · Ry(-π/2)⊗I · Rx(-π/2)⊗Rx(-π/2)
              · MS(π/2) · Ry(π/2)⊗I                           QCCDC.cx_decomp
```

The first is a **cost** result as much as a correctness one: stated in the hardware's
phase-gate convention it needs *no global phase at all*, and it says a single-qubit gate
is one frame update plus **exactly one physical pulse**. A naive Euler (Z-X-Z) reading
would emit three, tripling the single-qubit cost of every program the compiler produces.

Neither was quoted. `bridge/derive_pulses.py` searched for the constants — and the CX
phase came out `e^{-iπ/4}`, the opposite sign from the first guess, which is exactly why
the literature's three conflicting conventions are a reason to prove rather than remember.

**What is proved, and what is not.** Lean covers the two primitives above, plus the two
involutions (`axis_sq`, `XX_sq`) that license writing the gates in closed form instead of
as matrix exponentials at all. Everything else — `cz`, `cy`, `ch`, `swap`, `crz`, `cu1`,
`cp`, `rzz`, `ccx`, `cswap`, and user-declared `gate` bodies — is *defined* as a
composition of those two primitives, and each is checked numerically against its defining
unitary at full width by `Gateset_composites.check`. That check is not decoration: it
caught a real defect, `rzz` wrong by a phase of 2.0, because OpenQASM's `rz` is a phase
gate whose global phase stops being global the moment it sits inside a `CX · rz · CX`
conjugation. Promoting the composite identities to Lean is open work, recorded rather
than blurred.

#### The original design note



The native set of a trapped-ion machine, matching the `ms_gate` / `1q_gate` primitives the
architectures already declare:

```
MS(θ)   = exp(-i θ/2 · X⊗X)        the Mølmer–Sørensen entangler
R(θ,φ)  = exp(-i θ/2 · (cos φ X + sin φ Y))
RZ(θ)                              virtual — a phase bookkeeping change, zero duration
```

Each supported QASM gate gets one theorem of the form

```lean
theorem cx_decomp : CX = Complex.exp (I * α) • (U₄ * U₃ * U₂ * U₁) := by ...
```

These are concrete 4×4 complex matrices. `Matrix.ext` + `Complex.ext` + `norm_num`/`ring_nf`
discharges them; Mathlib has everything needed. **The global phase `α` is the point of doing this in
Lean rather than on paper** — the standard textbook CX↔MS identity is quoted with three different
phase conventions in the literature, and a wrong phase is invisible in isolation but observable
under control. Lean pins it.

Coverage target: the Clifford+T set plus arbitrary-angle rotations — `x y z h s sdg t tdg rx ry rz
u1 u2 u3 cx cz swap ccx`. `ccx` decomposes to 6 MS gates; that one is worth proving rather than
trusting.

The OCaml decomposition table is **generated from** the Lean statements (a `#eval` emitting JSON),
so the two cannot drift. Plus a numerical differential test: OCaml evaluates its own table's matrix
product and compares to the gate's matrix at 1e-12, on random angles.

### O1/O3 — a verified, executable checker (`QCCDC/Cert/`)

```lean
def check (c : Circuit) (a : Arch) (p : TSIR) (cert : Cert) : Bool := ...
theorem check_sound : check c a p cert = true → Implements p c
```

Written in Lean, proved sound, and **compiled to a binary** (`lake exe qcheck cert.json`). No
extraction step, no second implementation to trust. The OCaml checker exists only as a fast
pre-flight during compilation and is differential-tested against the Lean one — if they ever
disagree, Lean is right by construction.

This is the piece that turns R10 from `skipped` to `passed` honestly.

### Rules by construction (`QCCDC/Rules/`)

Some rules the compiler can *guarantee* rather than test. Example, already true of the existing ring
router and asserted only in a docstring today: a monotone one-direction sweep satisfies R5 and R11
by construction. Proving `∀ schedule, monotone schedule → satisfies_R5 schedule` converts a comment
into a theorem and lets the compiler skip a runtime check it can no longer fail.

Deliberately *not* in Lean: the cost model, the heating physics, the SAT encoding's optimality. Those
are empirical or performance claims, not correctness claims, and Lean would only lend them false
authority.

---

## 9. Layout

```
Compiler/
  PLAN.md                    this file
  README.md                  how to build and run all three toolchains
  ocaml/
    dune-project             (lang dune 3.0)
    ocamlenv.sh              PATH/switch activation — the trap verifier-ml already documents
    lib/
      qasm/      lexer.ml parser.ml ast.ml        1 parse
      circuit/   dag.ml commute.ml                2 DAG
      native/    gateset.ml table.ml              3 decompose  (table generated from Lean)
      arch/      arch.ml                          expanded .arch.json reader
      place/     spectral.ml anneal.ml            4 place
      route/     tegraph.ml encode.ml solve.ml    5 route  (SAT)
                 heur.ml window.ml
      simd/      aggregate.ml                     6 aggregate  (R4/R4b/R4d)
      opoint/    lambda.ml                        7 opoint
      emit/      tsir.ml cert.ml                  output
      check/     checker.ml                       fast pre-flight (mirrors Lean)
    bin/qccdc.ml               the CLI
    test/                      unit + differential
  lean/
    lakefile.toml              pinned to v4.29.0-rc2 + mathlib 3542f17d
    QCCDC/Pulse/               Native.lean Decompose.lean       O2
    QCCDC/Cert/                Syntax.lean Check.lean Sound.lean O1/O3
    QCCDC/Rules/               Static.lean
    Main.lean                  `lake exe qcheck`
  solver/
    solve.py                   UNTRUSTED z3/pysat bridge: SMT-LIB/DIMACS in, model JSON out
  bridge/
    export_arch.py             expanded .arch.json for OCaml
    check_tsir.py              run qccd.verify on emitted TSIR, report all 23 rules
  bench/
    *.qasm                     ghz, qft, bb144_esm, hgp225_esm, random Clifford
  docs/
    cert.md pulses.md sat.md
```

---

## 10. Milestones

Each has an acceptance criterion that can *fail*. Following the house style: a check that cannot
fail is not a check.

| M | deliverable | acceptance criterion |
|---|---|---|
| **C0** ✅ | scaffolding, toolchain pinning, JSON round-trip | **DONE.** OCaml reads all 9 expanded architectures (with a degree self-check against the document) and round-trips 13 TSIR fixtures with every field preserved in order; `qccd.verify` replays the OCaml-written deck schedule to **cost 397 184 / steps 8 808**, 17 rules passing. `bash Compiler/run_c0.sh`. *No compiler logic — this proves the interface before anything is built on it.* |
| **C1** ✅ | QASM front end + DAG | **DONE.** `bash Compiler/run_c1.sh`: **507/507** circuits agree with `circuit_to_dag` on registers, flattened ops, per-wire order and edge set — 13,144 ops / 14,894 edges, plus a 100,200-op circuit matched separately. Seven mutation classes are injected to prove the comparator can fail (**28 caught, 0 missed**). |
| **C2** ✅ | native gate set + **Lean O2 theorems** | **DONE.** `bash Compiler/run_c2.sh`. Six theorems, `#print axioms` shows only `propext / Classical.choice / Quot.sound` — no `sorryAx`. The OCaml table agrees with the defining unitaries to **2.0e-15** across 10,000 random `u3` triples, 1,000 random two-qubit angles and 24 gate forms; all **508** corpus circuits decompose with no unsupported gate. |
| **C3** | place + heuristic route + aggregate → **first end-to-end compile** | `ghz.qasm` and `qft8.qasm` compile onto `stationary_chain`, `chain72`, then `ring144_24v` and `grid9x9`. Emitted TSIR passes **all 23 rules**, and **R10 is reported `passed`** on the certificate. This is the project's headline. |
| **C4** ✅ | **SAT routing** | **DONE.** `bash Compiler/run_c4.sh`. Optimality is *proved* (the makespan is raised from 0, so the first SAT value is minimal by construction), and every returned schedule is re-checked against the constraints independently of the encoder. Results in §11.6. |
| **C5** ✅ | numerical layer: placement relaxation, the (runtime, error) frontier | **DONE.** `bash Compiler/run_c5.sh`. The frontier spans **2.19× in runtime for 22.5× in gate error** over 6 distinct points. Spectral placement **lost** to greedy on every instance and is kept only as a fallback; the hill-climb on the true objective won instead, by up to 12%. §11.7. |
| **C6** ✅ | **Lean verified checker, executable, end-to-end** | **DONE.** `bash Compiler/run_c6.sh`. `qcheck` is written in Lean, proved sound (`QCCDC.Cert.check_sound`, axioms: `propext` only), and compiled — no extraction, nothing trusted twice. **R10 `passed` on 13 of 15 programs**; the mutation suite seeds 11 defects and the proved checker catches **10 of the 10 it claims, 0 missed**. §11.8. |
| **C7** ◐ | oracle reproduction | **The vocabulary gap is closed.** The general router stops at 46% loop occupancy, so it could not compile BB[[144,12,12]] on `ring144_24v` at all; `qccdc rotate` now does, at 776 hops against the shipped pipeline's 2 672, with all 20 checkable rules and R10 passing (§11.9). Cyclone's external oracle is still open, which is why this is ◐ and not ✔. |

C0 and C7 are the oracles. Nothing between them is trusted until both pass.

---

## 11. Risks, and what I would do about each

| risk | probability | mitigation |
|---|---|---|
| **SAT does not scale even windowed** | medium | the three-tier design means the heuristic always ships; SAT degrades to an oracle on downsampled instances, which is still a publishable result. The plan does not depend on SAT scaling. |
| **Lean checker soundness proof is harder than it looks** | medium | stage it: C3 ships with the OCaml checker and R10 passing *on the OCaml checker's word*; C6 replaces that word with a proof. R10's status is honestly reported as `partial` in between — the house already has a `partial` bucket and uses it. |
| **Mathlib rebuild** | low but expensive | pin toolchain + rev to the prebuilt cache. Verified: 7,676 oleans present. Check this before the first `lake build`. |
| **OCaml on Windows/MSYS friction** | medium | `ocamlenv.sh` already solves it next door; copy it. Hello-world dune+yojson build confirmed working before writing this plan. |
| **The general compiler is worse than the specialised one** | **high, and expected** | this is *fine* and C7 states it as a 15% tolerance rather than parity. A general compiler that lands within 15% of a hand-tuned ring router while also handling grids is the result. Hiding the gap would be the failure. |
| **Scope: universal circuits vs Clifford** | — | see §12; this is a decision, not a risk |

One defect found while reading, worth fixing early because the compiler will trip on it:
`qccd/cost/models.py:314` maps every unrecognised gate name to `ms_gate`, so once the compiler emits
native 1Q pulses they will be **priced as two-qubit MS gates**. The `1q_gate` primitive is declared
in every architecture and read by nothing. Small fix, but it must land before C3's numbers mean
anything.

---

## 11.9 C7: the general router's range, and what rigid rotation buys

The plan expected C7 to report a percentage: the general compiler within 15% of the
specialised one on BB[[144,12,12]]. The measurement says something more useful.

**It cannot compile it at all**, and the reason is structural. Written with ancilla reuse
— 144 data plus 24 ancillas recycled through mid-circuit measure and reset, which is what
the shipped pipeline effectively does — the round needs **168 qubits** on a device with
168 traps and 312 ion slots. Sweeping the size on that device:

| qubits | occupancy | result |
|---:|---:|---|
| 144 | 46.2% | compiled |
| 156 | 50.0% | `unroutable: q143 cannot reach A78 from S134` |
| 168 | 53.8% | `unroutable: q155 cannot reach A72 from S5` |

**The individual-ion router reaches 46% occupancy on a closed loop and stops.** Every hop
needs a free slot at its destination; past about half-full on a ring, a path across the
device is a traffic jam that prioritised planning with parked obstacles cannot clear.

A rigid-rotation router has no such limit. One `loop_shift` template advances *every* ion
at once, so occupancy never changes and no path is ever blocked — which is exactly
`docs/PLAN.md` §1's thesis, and here it shows up as the difference between compiling and
not. The specialised pipeline's 327 instructions are not 15% better than the general
compiler's; they exist where the general compiler's do not.

That named the next feature precisely — **rigid rotation as a movement primitive**,
alongside individual hops — and it is now built.

### 11.9a Rigid rotation, and BB[[144,12,12]] compiling

`ocaml/lib/conveyor.ml` detects the shape (a closed loop with gate-capable *docks* hanging
off it by one spur) and `ocaml/lib/rotate_pipeline.ml` compiles to a three-word vocabulary:
**rotate** the loop, **dock** an ion off it, **undock** it back. The invariant that makes
it sound is that the loop never turns while a dock is occupied — a docked ion is off the
loop, so rotating underneath it would silently change which slot it returns to.

Its limit is the mirror image of the general router's: rigid rotation preserves the cyclic
*order* of the riders, so two ions on the loop can never meet. It therefore applies when
the interaction graph is bipartite with one side small enough to sit at the docks — which
is what a syndrome-extraction round is, and why the shipped ring was built for one.
Anything else falls back to the general router -- and `compile` wires the two together in
that order: the general router first, rotation only once it has declined. That direction
matters. Rotation is better on a ring for the circuits it serves, but preferring it would
silently change every program that already compiled; trying it second can only add.

BB[[144,12,12]] on `ring144_24v`, which the general router cannot compile at all:

| | rotation pipeline | shipped Python pipeline |
|---|---:|---:|
| contacts | 864 | 864 |
| hops | **776** | 2 672 |
| batches | 546 | **396** |

Different trade-off rather than a win: fewer hops (less transport heating), more batches
(more cycles). All 20 checkable rules pass; R10 passes.

Getting the batch count there took three passes, and the order is worth recording because
only the last is geometric. Emitting in program order costs 864 rotations and 19 464 hops.
A readiness scheduler — serve whatever is reachable now — gets to 788 batches / 4 956 hops.
Letting `cx` gates that share a control (or share a target) commute gets to 637. A
**monotone sweep** — always turn the same way, to the nearest offset ahead — gets to 546 /
776, because on a ring the cost is the *turning*, and a scheduler that greedily picks the
nearest target spends its life reversing.

### 11.9b What it cost the checker

Two things, and both were caught by the checker rather than by testing.

**The certificate could not carry it.** Expanding 546 rotations into a move per ion per
unit hop gives 113 472 moves, and `posAt` was a fold over the move list — quadratic, and
on this input it did not finish in fifteen minutes. `Rot` is now a first-class witness and
the replay is materialised once as a list of snapshots. The certificate drops from 7.7 MB
to 320 KB and the proved checker decides it in **5m14s**. The loop's node order travels
from the *architecture* (`mk_qcheck_input.py` reads `geometry.loops`), so the speed costs
nothing in trust: a compiler still cannot describe a loop of its own.

**The first version was wrong twice, and `check` said so.** It emitted the ancilla
Hadamards as pulses but never wrote gate witnesses for them — invisible to the tableau,
which composes from the pulses that *were* emitted, and caught immediately by `Covered`.
And the commutation the scheduler relies on is a real reordering, which `RespectsOrder`
rejected outright. The fix was not to weaken the rule but to state the exemption and prove
it: `Cert/Commute.lean` proves that two `cx` gates commute when they share a control with
distinct targets, or share a target with distinct controls, and exhibits a state on which
a control/target chain does *not* commute. `Commutes` in the checker admits exactly those
two cases. `bridge/mutate_cert.py`'s `swap_dependent_gates` — which exchanges which op two
witnesses realise, leaving co-location untouched — confirms the rule is not too generous.

What generality *did* buy is in the matrix below: 9 examples × 9 architectures, **72 of 81
pairs fully verified and 0 defects**, on grids, ladders, racetracks, chains and a two-trap
device — none of which the specialised ring router can target at all. The 9 that are out
of reach are a device too small (`stationary_chain` has 2 traps) or the heuristic router
declining; none is a rule violation.

---

## 11.8 C6: R10 is `passed`

`qccd/verify` has listed R10 — *"the compiled program implements the input circuit"* — as
UNCHECKABLE since the platform existed, for one stated reason: *"needs symbolic
permutation + Pauli-frame tracking against a QASM DAG"*. It is now checked, and by
something small enough to trust.

### The shape of the argument

`Implements` is a **Prop**, written in terms of `posAt` — a replay of the certificate's
move list from the initial placement. It never mentions the checker. `check` is then
*nothing but* `decide (Implements inp)`, so `check_sound` is `of_decide_eq_true` and all
of the content sits where a reader can judge it: in whether the specification says the
right thing. A checker written as a pile of `Bool`s with a meaning asserted elsewhere is
the arrangement that hides its assumptions; this one cannot.

Seven conjuncts: nothing unrealised, the qubit→ion map injective and total, the move list
in cycle order, **every move a hop the *architecture* admits and departing from where the
ion actually is**, every gate's operands co-located at a gate-capable trap, every op
covered by exactly one witness, and ops sharing a qubit realised in program order.

The fourth is the one that keeps the rest honest. Without it, every later check could be
satisfied by teleporting ions into position.

### Who supplies what

The compiler produces the certificate. It does **not** get to say which traps can gate or
which pairs are one cycle apart — `bridge/mk_qcheck_input.py` reads those from the
architecture document, by code the compiler does not run. If that derivation and the
compiler's trap graph ever disagree, the checker sees moves outside its hop set and
**rejects**: the failure is in the safe direction, which is what makes the duplication
worth its cost.

### The mutation suite

| caught by the proved checker | |
|---|---|
| `drop_gate`, `duplicate_gate` | coverage |
| `shift_gate_time`, `reorder_gates` | co-location at the replayed positions |
| `teleport_move`, `wrong_departure` | the hop relation |
| `ungateable_site` | R6 at the witness trap |
| `alias_qubits`, `mark_unrealised`, `unsort_moves` | map, coverage, replay well-definedness |

**10 of 10 caught, 0 missed** (measured on `ring144_24v`, where `ungateable_site` has
somewhere illegal to go — on a bare grid every trap can gate and that mutation has nothing
to mutate).

The eleventh is the interesting one. `swap_operands` exchanges a gate's two ions, and the
proved checker **accepts it** — correctly, because swapping them changes neither ion's
position and O1 does not claim to see operand *order*. A CX is not symmetric, so this is a
real bug, and it is caught by the **stabilizer tableau**, the other half of R10. Verified
rather than asserted: the tableau reports MISMATCH on exactly that mutation. Reporting the
split is more useful than tuning the suite until one layer catches everything.

### What `passed` does and does not mean

R10 `passed` means O1 was decided by a checker proved sound in Lean, and O2 by a stabilizer
tableau composed from the *emitted pulses*. The tableau itself is not proved — it is
`stim` plus a translation — so the honest reading is: the transport and co-location half
carries a proof, the semantic half carries an independent computation. Circuits outside
the Clifford fragment still report `partial`, because there the tableau says nothing and
saying nothing is not the same as passing.

---

## 11.9b The three questions, answered

**1. Arbitrary QASM onto any architecture, with performance and an animation?**

Yes, within stated limits. 507/507 parse tests against `circuit_to_dag`; the supported
gate set is the `u3` family, the controlled family, `ccx`/`cswap` and user-declared
`gate`s; multi-qubit gates are lowered before routing. All **9** shipped architectures are
targets, and **72 of 81** (circuit, architecture) pairs compile and verify. Performance
comes from the platform's own replay — cost, machine steps, wall-clock µs, gate error,
per-ion n̄ — and `bridge/render.py` writes the same self-contained animated page the rest
of the platform uses, driven by the **replay** rather than by the compiler's account of
itself. Not supported: `if` (parsed, not compiled), `opaque`, and circuits above ~46%
occupancy on a closed loop (§11.9).

**2. Are the conflicts modelled sharply enough that a bad program is rejected?**

Demonstrated, not asserted. `bridge/mutate_program.py` injects one hardware conflict at a
time into a program the verifier has already accepted:

| injected | rejected by |
|---|---|
| three ions in a capacity-2 trap | R1 |
| two ions on one segment in one cycle | R2, R3 |
| head-on swap along a segment | R3, R5 |
| a gate on operands that are not co-located | R6b |
| a gate in a zone whose type forbids it | R6 |
| two gates in one trap in one cycle | R12 |
| every cooling operation removed | R7, R7c |
| transport and a gate in one cycle | R4b |
| an ion landing where its `via` does not reach | refused at replay |

**9 of 9 rejected** on `ring144_24v`, 8 of 9 on `grid9x9` (where every trap can gate, so
the R6 case has nothing to violate). The rules are the platform's, not mine.

The router is aware of the same limits *while routing*: the reservation table in
`route.ml` enforces R1, R2, R3, R4d, R5 and R8 as it plans, R13 is folded into the
capacity bound, and every layer plan is replayed against occupancy before being returned.
It is optimised — space-time A-star, several priority orders, occupancy-aware gate siting,
a hill-climbed placement — and C4 measured that optimisation against an exact SAT encoding
of the same constraints: **optimal on every instance** of `clifford12` on `grid9x9` and on
`cyclone_base`. The SAT solver is the oracle, not the production router; that split is
deliberate (`Compiler/PLAN.md` §6's three tiers) and the gap it measures is the number
that justifies it.

**3. Do the pulses actually compute the input circuit?**

Yes, and by two independent routes:

* **Clifford circuits** — the emitted `R`/`VZ`/`MS` instructions are composed into a
  stabilizer tableau through the certificate's qubit→ion map and compared with the source
  circuit's. This scales, and it subsumes Pauli-frame tracking: a frame error *is* a
  tableau difference.
* **Non-Clifford circuits** — an exact `2^n × 2^n` unitary comparison, up to one global
  phase, for `n ≤ 10`. `qft8` matches to **4.3e-16** (256×256) and `adder3`, which is
  nothing but Toffolis and `T` gates, to **3.1e-15** (1024×1024).

Both read the *emitted hardware program*, never the compiler's intent. With the unitary
route in place the matrix has **no `partial` rows left**: every program that compiles is
also proved to implement its circuit.

---

## 11.85 The verification matrix

`python Compiler/bridge/run_matrix.py` — every example on every shipped architecture,
compiled, cooled, checked against all 23 rules, and R10 decided by the proved checker.

```
81 (circuit, architecture) pairs
  72 fully verified   -- compiled, all rules pass, R10 passed
   0 partial
   9 out of reach     -- device too small, or the heuristic router declined
   0 DEFECTS          -- a rule violated or R10 refused
```

Keeping those four buckets apart is the point. A compiler that reported the last three as
"failed" would hide which of them are bugs — and the answer is none of them.

Two fixes came out of building it, both systematic rather than incidental:

* **Multi-qubit gates never reached the router.** A Toffoli needs three ions in one trap,
  which most devices cannot host, so `adder3` was unrealised everywhere. Gates are now
  *lowered* to 1- and 2-qubit gates before placement. The lowering is not trusted: R10's
  stabilizer half compares the emitted pulses against the **original** circuit, so a wrong
  expansion is a tableau mismatch.
* **Placement ignored gate capability.** On `cyclone_dual_loop` the two loops are
  disconnected and only one can gate; every qubit went to the wrong loop and every op came
  back unrealised — correctly, and uselessly. Placement now tiers by capability, and
  chooses its tier by **capacity** rather than trap count: `ring144_24v` has 144
  non-junction traps of capacity 2, and counting traps instead would fall through to the
  tier containing all 24 chokepoints.

---

## 11.7 C5: what the numerical layer actually bought

### The (runtime, error) frontier — the headline

`docs/PLAN.md` §0.2 argues that cost and steps are two named halves of one error budget,
and §0.3 that transport causes no gate error directly — it heats, and heating degrades the
*next* gate. That makes cooling frequency a continuous exchange rate, and sweeping the R7
budget traces it (`clifford12` on `grid9x9`):

| R7 budget | runtime | gate error | cools | peak n̄ |
|---:|---:|---:|---:|---:|
| 64 | **18.21 ms** | 3.8486 | 1 | 61.7 |
| 32 | 28.11 ms | 0.9215 | 36 | 32.3 |
| 16 | 31.11 ms | 0.4420 | 46 | 16.1 |
| 8 | 36.51 ms | 0.2162 | 64 | 16.1 |
| 4 | 38.01 ms | 0.1776 | 69 | 16.1 |
| 1 / none | 39.81 ms | **0.1711** | 75 | 16.1 |

**2.19× in runtime for 22.5× in gate error**, and every point between is reachable.

The honest finding about the three shipped policies is *not* that they are dominated —
`fastest`, `coolest` and `balanced` all land on the frontier, at its slowest and most
accurate end. What they are is **not a choice**: they give no access to the other half of
the curve. That is a sharper criticism than "suboptimal", and it is the one the data
supports.

One platform fix was needed to measure this at all: `verify` accepted no `rule_config`,
though `replay` has always taken one and `qccd/compile/cooling.py` has always passed one.
Without it every swept point is judged against the architecture's *original* budget and
reads as an R7 failure — the sweep changes the schedule and then reports nothing about the
trade-off it exists to measure.

### Placement: the relaxation lost

The plan predicted a spectral (Fiedler) seed would beat greedy weighted insertion. **It did
not — on any instance:**

```
ghz8/grid9x9      greedy  7   spectral 10        clifford12/cyclone  greedy 198  spectral 249
ghz32/cyclone     greedy 31   spectral 496       qft6/ring144_24v    greedy  44  spectral  46
```

The reason is visible in the numbers: the Fiedler embedding is one-dimensional and these
devices are not, while the greedy already places against *true* trap distances. Predicting
otherwise was reasonable; keeping the prediction after measuring it would not be. Spectral
is retained only as a placement fallback.

What did win is the **hill-climb on the true objective** — pairwise exchanges, up to 12% on
the dense circuits (`clifford12` on `cyclone_base`: 198 → 175). Measured through to
makespan: `qft6` on `h2_racetrack` 116 → 82 cycles, `clifford12` on `cyclone_base` 322 →
296.

And one thing the improvement broke, which is worth recording because it is a general
hazard: a *better* placement by interaction distance made `clifford12` on `ladder_2x72`
**unroutable**. Placement optimises distance; the router has to live with the result, and
those are not the same objective. The compiler now falls back to the runner-up placement
when routing fails, which costs one recompile and restores it.

---

## 11.6 C4: the optimality gap, and the price of broadcast control

Measured on the routing sub-problems the compiler actually solved, re-solved exactly on
the identical graph — the graph travels *with* the instance, because a gap measured
against a solver that rebuilt it slightly differently is not a gap, it is two different
problems.

| circuit / device | regime | heuristic vs optimal | broadcast price |
|---|---|---|---|
| `clifford12` / `grid9x9` | free (direct-wired) | **0.0%** — 8/8 optimal | **0 cycles**, binds on 0/8 |
| `clifford12` / `cyclone_base` | conveyor | **0.0%** — 8/8 optimal | **5 of 29 cycles (17%)**, binds on 4/8 |
| `qft6` / `h2_racetrack` | conveyor | 102.7% — 7/8 optimal, worst **+38** | **9 of 37 cycles (24%)**, binds on 8/8 |

**The price of broadcast control is real and it is large.** One waveform per named loop
costs 17–24% of makespan on the conveyor devices, and on the racetrack it binds on *every*
instance. `grid9x9` is the control: it declares no named loop, so constraint 3 cannot bind
there, and the measurement duly comes out at exactly zero. A non-zero reading there would
have meant the measurement was wrong rather than the device was slow.

**The heuristic is already optimal wherever the problem is not adversarial** — every
instance on the grid and on `cyclone_base`. The exception is instructive: on a closed loop
the router parks an ion at its goal for the rest of the layer, and if that goal sits on the
short arc between another ion and *its* goal, the second ion goes the long way round. The
oracle caught two such instances at 43 and 39 cycles where 5 suffice. Trying several
priority orders and keeping the best fixed one of them and halved the aggregate gap
(194.6% → 102.7%); the survivor needs the simultaneous coordination only the exact solver
does, which is precisely the case for having built it.

**Tier boundary, as predicted.** `ring144_24v`'s sub-problems have heuristic makespans of
20–56 cycles, past what a monolithic encoding reaches — §6's "exact tier" is exact about
its own limits, and the windowed tier is what would extend it.

---

## 11.5 What C3 changed in the platform, and what it found

Three additive extensions to TSIR were needed before a general circuit could be expressed
at all, each of which also closed a latent defect:

* **a one-operand `gate`** — the IR's `gate` was inherently two-qubit, so it could express
  an ESM schedule and nothing else. `iter_operands` now serves the rules that are about
  the *operation* (R6, R12); `iter_pairs` still serves the ones that are about a *pair*
  (R6b, R7), so no two-qubit check changed meaning.
* **`arity`** — a batch of single-qubit gates driven together. Without it, 144 beams at
  144 different traps would have been 144 machine cycles instead of one, since the
  two-ion spelling is ambiguous between "one pair" and "two singles".
* **`params`** — `R` with no angle is not a hardware instruction. This also let
  `gate_1q` read the `1q_gate` primitive that every architecture declares and *nothing
  had ever read*: before this, a single-qubit rotation would have been priced as a
  two-qubit entangling gate.

Four defects the checks caught, each by a check that existed to catch it:

1. **The router discarded its own timing.** `plan_one` returned hops stripped of the
   cycle they depart in, and the assembler re-indexed them by list position — throwing
   away every wait the reservation table had computed. Two ions crossed one segment in
   opposite directions in one cycle, in a router whose entire purpose is that they cannot.
   Found by the verifier (R3, R4, R5 all fired at once).
2. **Junction traps.** R2 caps a degree-≥3 node at one ion whatever its zone declares,
   and the shipped ring's 24 dock rail slots are both a capacity-2 `data` trap and a
   degree-3 junction. `Arch.eff_capacity` now owns that.
3. **Parking was checked at the arrival instant only.** An ion settles into its goal for
   the rest of the layer, so the goal must be free at *every* later cycle — otherwise it
   settles into a trap another ion is still going to transit. Three ions in a capacity-2
   trap, every individual check passing.
4. **The compiler dropped the virtual-Z frames.** They were recorded in the certificate
   and absent from the emitted program — so the program did not implement the circuit.
   A Z frame does not commute through the MS entangler, so it cannot be pushed to the end
   either. Found by R10's own stabilizer check, which is exactly obligation O3.

And one measurement worth keeping: **on `ring144_24v` only 24 of 168 traps can gate**, so a
general circuit pays for a round trip per gate — 110 transport cycles for `ghz8` against 7
on a grid. That is a property of the architecture, surfaced by compiling to it.

---

## 12. Decisions — resolved 2026-08-23

**D1. Circuit scope: Clifford-first, universal later.**
Non-Clifford gates parse and route, but O1/O3 are discharged by **stabilizer simulation** (`stim`)
on the Clifford fragment. This is a strictly better check than the gate-by-gate symbolic route where
it applies: it verifies the *whole program end to end* — compile the circuit, read the compiled
program's induced Clifford back out of the certificate, and compare stabilizer tableaus. It also
subsumes O3 for free, since a Pauli-frame or operand-swap error shows up as a tableau mismatch
without any separate frame checker. Consequence for §8: the Lean `Cert/` development checks the
*transport and co-location* obligation (O1), and the tableau comparison carries the semantic one.
Universal circuits fall back to the §8 symbolic route and are reported `partial` until then.

**D2. Relationship to `qccd/compile/`: alongside, as a second backend.**
The Python BB/ring pipeline stays and remains the differential oracle for C7. The OCaml compiler is
optimized for **generality across all 9 architectures**, and is expected to lose to a hand-tuned ring
router on the ring — C7's 15% tolerance encodes that, and the gap gets reported, not hidden.

**D3. Lean depth: ship C3 on the OCaml checker, prove at C6.**
R10 is reported **`partial`** between C3 and C6, with the reason "checked by the OCaml checker, not
yet by the proved Lean one" — the same reporting contract `docs/rules.md` already applies to R15. It
becomes `passed` when `lake exe qcheck` and the mutation suite land at C6. R10 is never reported
`passed` on an unproved checker.

Next action: **C0**, which none of the three decisions affects — it proves the OCaml↔Python interface
against the 397 184 / 8 808 oracle before a single line of compiler logic exists.
