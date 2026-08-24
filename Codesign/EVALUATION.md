# How to score a design without simulating it

The objective for the codesign loop, what the literature does, and the three places this
repository's model is missing a term that the search would otherwise exploit.

**Summary.** The metric this repo already computes — `neg_log_fidelity`, the expected number
of faults in a syndrome round — is the right one, and it is the same construction the
closest published precedent uses. Three things have to be fixed before it can rank
architectures honestly, and one of them (no chain-length term in the gate error) would
otherwise hand the optimiser a free lunch and make it report a fake optimum.

---

## 1 · What the repository already has

[`docs/PLAN.md` §6](../docs/PLAN.md) defines a three-tier objective and
[`qccd/cost/physical.py`](../qccd/cost/physical.py) implements tier T2:

```
−ln F  ≈  Σ_gates ε(n̄_at_gate)  +  N · T_exe / T_coh  +  Σ_spam ε_spam
          └── gate_error_sum ──┘   └── idle_error ──┘   └── spam_error ┘
```

Everything needed is already emitted per run: `runtime_us`, `n_gate_pairs`,
`mean_gate_quanta`, `peak_quanta`, `quanta_per_data_ion` **split by channel**
(shuttle / junction / split_merge / gate / anomalous), and `junction_transits_per_data_ion`.
[`qccd/analysis/budget.py`](../qccd/analysis/budget.py) additionally attributes the error to
each heating channel with an **exact two-point derivative** — which is what a coordinate-descent
proposer wants for free.

**This is not a placeholder. It is the same figure of merit the field uses.**

## 2 · What the literature does

### The methodological precedent

Murali, Debroy, Brown and Martonosi, *Architecting Noisy Intermediate-Scale Trapped Ion
Quantum Computers*, ISCA 2020 — the closest published analogue to this study. They evaluate
QCCD architectures by **multiplying per-operation fidelities over the whole program**, with
no quantum simulation, and sweep architectural parameters against it. Their gate model:

```
F  =  1 − Γτ − A(2n̄ + 1)          A ∝ N / ln N
```

| term | meaning | in this repo? |
|---|---|---|
| `Γτ` | background heating during the gate, τ = gate duration | folded into a constant `ε₀` |
| `A · 2n̄` | motional excitation at gate time | **yes** — `ε₀ + k·n̄` |
| `A ∝ N/ln N` | laser-intensity instability grows with **chain length N** | **no — absent entirely** |

Their quantitative transport figures: **~0.1 quanta per split/merge, ~0.01 quanta per
segment traversal** — useful as an order-of-magnitude check against this repo's primitive
curves, which encode the same (µs, quanta) trade-off per operation.

Their headline results, both directly relevant:

- **Trap capacity has an optimum at 15–25 ions.** Small traps shuttle too much; large traps
  lose to the `N/ln N` term and become motional hot spots.
- **Trap sizing and topology move application reliability by up to three orders of
  magnitude.** The thing we are searching over is the thing that matters most.
- Gate duration scales differently by gate type: **AM** ∝ ion separation, **FM** ∝ chain
  size `N`, **PM** weakly distance-dependent.

### The shuttling error budget

Kaushal *et al.*, *Shuttling-Based Trapped-Ion Quantum Information Processing* (AVS Quantum
Sci. 2, 014101, 2020) — the review for what the transport primitives cost. Operations divide
into **linear transport, separation/merging, and swap by crystal rotation**, which is exactly
this repo's movement-class vocabulary. Linear transport, separation and merging have been
demonstrated with no induced excitation on the transverse mode, supporting two-qubit gates
above 99.5% fidelity — so the (time, excitation) curve per primitive, which is how this repo
models it, is the right shape.

### Anomalous heating scales with geometry

Brownnutt *et al.*, *Ion-trap measurements of electric-field noise near surfaces*, and the
distance-scaling measurement in Phys. Rev. A **97**, 020302 (2018): electric-field noise goes
as **d⁻⁴** in ion–electrode distance, with the exponent holding at both room and cryogenic
temperature, and cryogenic operation lowering the rate by **~two orders of magnitude**.

This matters here more than in most studies, because
[`qccd/phys/field.py`](../qccd/phys/field.py) **solves for the ion height** from electrodes
derived from the layout. Heating is currently a constant in the architecture document; it
does not have to be.

### The code's own numbers, which remove the need for a decoder

Bravyi *et al.*, *High-threshold and low-overhead fault-tolerant quantum memory*, Nature 627,
778 (2024) — the `[[144,12,12]]` gross code:

| | |
|---|---|
| pseudo-threshold, circuit-level noise | **≈ 0.7 %** |
| syndrome cycle | **7 computational stages**, weight-6 checks, degree-6 connectivity |
| physical qubits | 288 (144 data + 144 check) |
| calibration point | at `p = 10⁻³`, logical error **≈ 2 × 10⁻⁷ per cycle** |

A published threshold plus a published `(p, ε_L)` point is a **transfer function we can use
for free**. No decoder, no sampling: given an effective per-location error rate, the curve
says roughly what the code does with it.

## 3 · The metric

### Primary — two numbers, both already computed

```
T_round   =  res.total_us                                    wall-clock for one ESM round
p_eff     =  neg_log_fidelity / L                            mean fault probability per location
              L = n_gate_pairs + n_idle_intervals + n_spam
```

`p_eff` is the number the 0.7 % threshold is quoted against, so **the first thing any
candidate reports is `p_eff` versus 0.7 %.** A design at 5 % is not a slow design; the code
does not help it at all and no amount of compiler work will fix that.

### Why time is a *term*, not a rival objective

[`docs/PLAN.md` §0.2](../docs/PLAN.md) already argues this and it is worth restating: cooling
more lowers `n̄` at each gate but lengthens the round, and a longer round costs idle
dephasing — which is already inside `neg_log_fidelity` as `N·T_exe/T_coh`. So **the runtime/error
trade is internal to one scalar**, and there is an interior optimum in the cooling budget
rather than a frontier to choose a point on. Measured span of that knob:
[**2.19× runtime for 22.5× gate error**](../Compiler/PLAN.md).

### Optional ranking scalar — a transfer function, never a simulation

To collapse `(p_eff, T_round)` into one number for a memory experiment, use the published
sub-threshold form calibrated at Bravyi's point:

```
ε_L(p_eff)  ≈  ε_ref · (p_eff / p_ref)^⌊(d+1)/2⌋        p_ref = 10⁻³, ε_ref = 2×10⁻⁷, d = 12
Λ           =  ε_L(p_eff) / T_round                     logical failures per second
```

**Label this an extrapolation everywhere it appears.** It is a literature curve evaluated at
our `p_eff`, not a measurement of our circuit. It is legitimate for *ranking* designs and
illegitimate as a claimed logical error rate.

### Report a bracket, not a point

The threshold is quoted for *uniform* circuit-level depolarising noise. Ours is deliberately
non-uniform — some ions are much hotter than others, which is the entire physics being
studied. Non-uniform noise at the same mean generally does **worse** than uniform, because
the worst locations dominate. So the mean `p_eff` is optimistic. Report both:

```
p_eff        mean over locations              optimistic
p_eff_90     90th percentile of per-gate ε    pessimistic
```

A design whose bracket straddles the threshold has not been shown to work. `peak_quanta` and
`max_gate_quanta_seen` are already emitted, so the pessimistic end is nearly free.

---

## 4 · Three gaps to close before the loop runs

### G1 · Gate error does not depend on chain length — **and this one is load-bearing**

```python
# qccd/cost/models.py
def gate_error(self, arch, nbar):
    return eps0 + float(value) * max(nbar, 0.0)      # no N anywhere
```

`grep` finds no chain-length dependence in the cost model at all. Trap capacity is one of
the architecture knobs we intend to search. With no penalty for large chains, **more ions per
trap is strictly better** — fewer shuttles, no cost — so the optimiser will drive capacity up
until it hits R13's hard cap of 15 ions and then report that cap as the optimum. That is not
a finding, it is the constraint being read back.

Murali *et al.* locate the real optimum at 15–25 ions, and it exists precisely because of the
`A ∝ N/ln N` term this model is missing. **Add it before searching over capacity**, together
with the `Γτ` term so that gate duration (itself `N`-dependent for FM gates) costs something:

```
ε(n̄, N, τ)  =  ε₀ + Γ·τ(N) + κ · (N / ln N) · (2n̄ + 1)
```

Calibrate `κ` and `Γ` so the shipped `ring144_24v` schedule reproduces its current error at
its current chain length — the change must be a *refinement* of the validated model, not a
replacement for it.

### G2 · Anomalous heating is a constant, but the geometry determines it

`anomalous_per_us` returns `arch.anomalous_rate()/1000` — a scalar from the document. Given
d⁻⁴ scaling and a solver that already computes `ion_height_um`, this can be derived:

```
Γ_anom(device)  =  Γ_ref · (d_ref / d_solved)⁴
```

The repo has already measured that **no shipped device sits at its design ion height —
neighbouring metal moves it by up to 15%**. Under d⁻⁴ that is a **1.15⁴ ≈ 1.75×** error in the
heating rate, on every ion, for the whole schedule. Not a detail.

This is the coupling that makes the study distinctive: **layout → electrodes → ion height →
heating → gate error**, derived end to end. A study without a field solver has to assume the
heating rate; this one can compute it.

### G3 · Idle error is linear in time; ion memory is often closer to Gaussian

```python
idle_error = len(res.per_ion_quanta) * (res.total_us * 1e-6) / t_coh_s
```

Markovian, linear in `t`, charged to every ion for the whole runtime. For hyperfine ion qubits
under correlated magnetic-field noise the decay is often closer to `(t/T₂)²`. Linear is the
conservative choice while `t ≪ T₂` and is defensible — but it is a **modelling choice that
directly sets the cooling optimum**, so run the loop under both and report whether the winner
changes. If it does, that is a finding about the study's sensitivity, not a bug.

---

## 5 · What this metric cannot tell you

State these wherever a number from it is reported.

- **It is not a logical error rate.** `−ln F` counts expected faults; it does not know the
  code, the decoder, or which faults are correctable. `ε_L` above is a literature curve
  evaluated at our `p_eff`.
- **It ignores error structure.** Correlated faults, hook errors, and leakage are invisible.
  A schedule that turns one ancilla fault into a weight-2 data error scores identically to one
  that does not — which is why **CX order within a check must not be optimised by this metric
  alone** (see `PLAN.md` CD2·1).
- **It ignores crosstalk and addressing error**, both of which grow with chain length.
- **The threshold comparison assumes uniform noise.** Hence the bracket in §3.
- **It cannot rank two designs whose brackets overlap.** Report them as tied.

---

## Sources

- [Architecting Noisy Intermediate-Scale Trapped Ion Quantum Computers (ISCA 2020)](https://arxiv.org/abs/2004.04706) · [ar5iv](https://ar5iv.labs.arxiv.org/html/2004.04706)
- [High-threshold and low-overhead fault-tolerant quantum memory (Nature 627, 778)](https://arxiv.org/pdf/2308.07915) · [gross code, Error Correction Zoo](https://errorcorrectionzoo.org/c/gross)
- [Shuttling-Based Trapped-Ion Quantum Information Processing](https://arxiv.org/abs/1912.04712)
- [Ion-trap measurements of electric-field noise near surfaces](https://arxiv.org/pdf/1409.6572)
- [Distance scaling of electric-field noise in a surface-electrode ion trap (PRA 97, 020302)](https://arxiv.org/abs/1712.00188)
- [Closed-loop optimization of fast trapped-ion shuttling with sub-quanta excitation](https://www.nature.com/articles/s41534-022-00579-3)
- [Scaling and assigning resources on ion trap QCCD architectures](https://arxiv.org/pdf/2408.00225)
