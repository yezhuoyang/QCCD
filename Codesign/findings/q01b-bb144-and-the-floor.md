# 0.1b · The BB round on nine devices — and why the objective barely moves

**Status:** answered. **Verdict:** the "circuits were too small" hypothesis is **dead**, and so
is the diagnosis it was competing with. The 1.3× spread is not the missing chain-length term
and not the circuit size — it is **the cooling pass converting heating into runtime, and
runtime being nearly free at `T_coh = 600 s`.** Three claims the plan rests on are falsified
here. `q01`'s headline number was also wrong, for a separate reason, and is corrected.

```bash
python Compiler/bridge/gen_bb144.py -o Compiler/examples/bb144_esm.qasm --ancillas 24
python Compiler/bridge/run_matrix.py --circuits bb144_esm --json Codesign/data/q01b_matrix.json
python Codesign/scripts/q01b_bb144_spread.py --json Codesign/data/q01b.json
```

73 (circuit, device) pairs, every one of them `ok` in the verification matrix — all 23
checkable rules passed and **R10 `passed` by the proved Lean checker**. That is the tier
`PLAN.md` CD5 requires of a number that reaches this directory, and it is the first thing
`q01` did not do.

---

## 1 · The BB round compiles on **one** of the nine devices

| device | slots | gate sites | occ @168 q | closed loops | outcome |
|---|---:|---:|---:|---:|---|
| stationary_chain | 64 | 2 | 262 % | 0 | too-small |
| h2_racetrack | 80 | 40 | 210 % | 1 | too-small |
| chain | 144 | 72 | 117 % | 0 | too-small |
| ladder_2x72 | 576 | 144 | 29 % | 0 | unroutable |
| grid9x9 | 288 | 144 | 58 % | 0 | unroutable |
| deck_unit_cell | 288 | 144 | 58 % | 0 | unroutable |
| cyclone_base | 288 | 72 | 58 % | 1 | unroutable |
| cyclone_dual_loop | 288 | 72 | 58 % | 2 | unrealised |
| **ring144_24v** | **336** | **24** | **50 %** | **1** | **ok** — 20/20 rules, R10 passed |

Two independent structural facts, neither of them about the error model, produce this:

- **Three devices cannot hold the circuit.** 168 qubits against 64, 80 and 144 slots. This is
  physics, not a compiler limit, and it is a legitimate result: a device that cannot hold a BB
  round is not a candidate for running one.
- **Rigid rotation is the only router that works past ~46 % occupancy** (`Compiler/PLAN.md`
  §11.9), and it needs a *closed loop with gate-capable docks off it*. Only `ring144_24v` has
  that shape. `qccdc rotate` declines on the other eight with *"the device has no closed loop
  with docks"* — including `cyclone_dual_loop`, whose two closed loops do not help because its
  gate sites sit **on** the loops rather than on spurs.

**So at the scale the study is about, the architecture comparison has N = 1.** `PLAN.md`
CD6's row *"the compiler cannot serve a family → comparison silently becomes 'which device
suits our router'"* has fired, in its strongest form.

A latent gap found while checking this, worth fixing but not load-bearing:
[`qccdc_cli.ml:360`](../../Compiler/ocaml/bin/qccdc_cli.ml#L360) tries rotation only when the
general router *raises* `Route.Unroutable`. `cyclone_dual_loop` instead returns normally with
`UNREALISED ops: 1272`, so rotation is never attempted. Invoking `qccdc rotate` on it by hand
declines anyway, so no device was lost — but the fallback is narrower than its comment claims.

## 2 · `q01`'s 48 % was SPAM, not the architecture

`q01` reported *"device-attributable share of `−ln F`: median 48 %"*. It defined the floor as
`n_pairs · ε₀` alone, so the whole SPAM term counted as the device's doing. All nine
architectures declare **identical** `ms_gate.fidelity_at_n0` (1.84e-3), `measure.fidelity`
(1.6e-3 infidelity), `reset.error` (5e-3), `T_coh_s` (600) and `anomalous_rate` (0.05/ms) —
asserted by the script, not assumed — so for a fixed circuit `n_pairs·ε₀ + spam` is a constant
no geometry can move. It is floor.

| | `q01` | corrected |
|---|---|---|
| device-attributable share of `−ln F` | 10 – 70 %, **median 48 %** | 0.1 – 23.5 %, **median 3.9 %** |
| programs measured | 39, from `Compiler/build/out/` | 73, from `Compiler/build/matrix/` |
| feasibility of those programs | unrecorded | all `ok`: 23 rules + R10 passed |

Two further defects in `q01`'s method, both fixed in `q01b`:

- **Wrong directory.** `Compiler/build/out/` is scratch output from `gen_bench.py` and
  friends. The verification matrix is `Compiler/build/matrix/`, with verdicts in
  `Compiler/build/matrix.json`. `q01` measured `broadcast_zoo`, `clifford12`, `clifford40`
  and `st` — none of which is in the example set — and carried no record of whether any of
  them was feasible.
- **Device-suffix matching.** `next(d for d in sorted(devices) if stem.endswith('_'+d))`
  attributes every `*_stationary_chain` program to `chain`, because `chain` sorts first and is
  a suffix of `stationary_chain`. It did not fire on `out/` (no such files) but would have on
  the real matrix.
- Also: `q01.json` recorded `quanta_per_data_ion` for all 39 rows and **every one is
  identically zero.** `t2_metrics(data_ion_prefix="d")` selects ions whose name starts with
  `d`; OCaml-compiled programs name them `q0…q167`. Those columns were vacuous.

## 3 · The spread does not grow with circuit size

| circuit | 2q gates | `−ln F` spread | device-part spread | device share |
|---|---:|---:|---:|---|
| bell2 | 1 | 1.04× | 68× | 0.1 – 3.9 % |
| ghz4 | 3 | 1.07× | 61× | 0.1 – 6.6 % |
| bv6 | 3 | 1.15× | 79× | 0.2 – 13.0 % |
| ghz16 | 15 | 1.21× | 48× | 0.5 – 17.9 % |
| rep9_esm | 16 | **1.30×** | 48× | 0.6 – 23.5 % |
| steane_esm | 24 | 1.25× | 84× | 0.3 – 20.4 % |
| surface17_esm | 24 | 1.07× | 19× | 0.4 – 6.5 % |
| adder3 | 42 | 1.29× | 78× | 0.4 – 22.9 % |
| qft8 | 68 | 1.21× | **121×** | 0.2 – 17.6 % |
| bb144_esm | 864 | *n/a — one device* | — | 5.7 % |

Over a 68× range in gate count the `−ln F` spread is flat and non-monotone: the widest is
`rep9_esm` at 16 gates, and the largest measurable circuit is *narrower* than it
(r = 0.48 across nine circuits, which at n = 9 is nothing). **Circuit size was not the
explanation.**

But look at the third column. **The device-attributable part alone spreads 19× to 121×** —
one to two orders of magnitude, the same scale Murali *et al.* report. The geometry signal is
not weak. It is being *diluted*, by a floor that is 96 % of the scalar it is measured inside.

## 4 · What is actually happening: cooling launders heating into runtime, and runtime is free

`bb144_esm` on `ring144_24v`, before and after the cooling pass:

| | runtime | mean gate n̄ | gate error | idle | SPAM | `−ln F` | `p_eff` |
|---|---:|---:|---:|---:|---:|---:|---:|
| uncooled | 358.5 ms | **322.1** | 558.24 | 0.100 | 0.830 | 559.18 | 0.115 = **16.4× threshold** |
| cooled | 522.3 ms | **0.000** | 1.590 | 0.146 | 0.830 | 2.566 | 5.27e-4 = 0.075× threshold |

The cooling pass drives the mean gate n̄ to **exactly zero**, so the gate-error term lands
**exactly on its floor** — `864 × 1.84e-3 = 1.5898`, to the last digit. The entire heating
signal is gone, converted into 45 % more runtime, and the only device-attributable term left
is idle dephasing at 5.7 % of the total.

This gets *worse* with circuit size, not better — the opposite of what `q01` guessed:

| circuit | 2q gates | residual mean gate n̄ after cooling | idle share of the device part |
|---|---:|---:|---:|
| ghz4 | 3 | 0.059 | 2.4 % |
| rep9_esm | 16 | 0.126 | 3.6 % |
| qft8 | 68 | 0.067 | 2.3 % |
| **bb144_esm** | **864** | **0.000** | **100 %** |

The more transport a circuit does, the more completely the R7 trigger fires and the more
thoroughly the heating is erased. A bigger circuit does not stress the model harder; it
launders more of itself into runtime.

### Two structural ceilings behind it

**R7 caps what geometry can do to the gate term, at 2.09×.** `ms_gate.max_quanta = 1.0` on
every architecture, and `error_vs_quanta = linear:2.0e-3`. So a gate in any *feasible* program
sees `ε ∈ [1.84e-3, 3.84e-3]`. Between two legal schedules the gate term can differ by at most
**2.09×, on any circuit, on any device, ever** — and the cooling pass puts them both at the
bottom of that interval. The 1.3× measured spread is not a small effect inside a large space;
it is most of the space there is.

**`T_coh = 600 s` makes runtime nearly free.** One global cool is 300 µs, and charged as idle
to 168 ions against a 600 s coherence time it costs `8.4e-5`. It saves up to `2.0e-3` on
*each* gate it protects. A cool repays itself once it protects **0.042 gates** — that is, a
24× return on the very first one.

## 5 · So there is no interior optimum in the cooling budget

`PLAN.md` CD0 and CD2·4 and `EVALUATION.md` §3 all assert one: cooling lowers n̄ but lengthens
the round, both terms live in the same scalar, so *"there is an interior optimum in the cooling
budget rather than a frontier to choose a point on"*, and finding it is *"the cleanest early
win in this whole plan."*

`c5_pareto`'s measured frontier, re-scored under the **full** objective rather than gate error
alone (`Compiler/build/c5_frontier.json`, idle = 168 ions × runtime / `T_coh`):

| R7 budget | runtime | cools | gate error | idle error | **total** |
|---:|---:|---:|---:|---:|---:|
| **1** | 39.81 ms | 75 | 0.17112 | 0.01115 | **0.18227** |
| 2 | 39.81 ms | 75 | 0.17112 | 0.01115 | 0.18227 |
| 4 | 38.01 ms | 69 | 0.17757 | 0.01064 | 0.18822 |
| 8 | 36.51 ms | 64 | 0.21615 | 0.01022 | 0.22638 |
| 16 | 31.11 ms | 46 | 0.44204 | 0.00871 | 0.45076 |
| 32 | 28.11 ms | 36 | 0.92149 | 0.00787 | 0.92936 |
| 64 | 18.21 ms | 3 | 3.84863 | 0.00510 | 3.85372 |

**Monotone.** Gate error falls 22.5× as the budget tightens; idle rises 2.2×, from a base an
order of magnitude smaller. The minimum is at the strictest budget, which is R7's cap, which
is what the shipped policy already does. Question **0.2 is answered, and the answer is that it
was not a question**: the optimum is a boundary solution and the three shipped policies that
"are not a choice" were already sitting on it.

**G3 cannot rescue it either.** `EVALUATION.md` §4 says the linear-vs-Gaussian idle law
*"directly sets the cooling optimum"*. It does not. At 522 ms against `T₂ = 600 s`,
`t/T₂ = 8.7e-4` and `(t/T₂)² = 7.6e-7` — the quadratic law makes idle **1150× smaller**, so it
makes cooling *more* free, not less. Under either law the optimum is at the boundary. G3 is a
real modelling question; it is not this one.

## 6 · What this does to the plan

| claim | status |
|---|---|
| "median 48 % of `−ln F` is device-attributable" (`q01`) | **wrong** — 3.9 %; SPAM was miscounted |
| "the circuits were too small" | **falsified** — spread is flat over 1→68 gates, and larger circuits erase *more* heating |
| "there is an interior optimum in the cooling budget" (CD0, CD2·4, EVAL §3) | **falsified** — monotone; the optimum is R7's cap |
| "the cooling optimum is the cleanest early win in this whole plan" (CD2·4) | **dead** — the shipped policy is already at it |
| "G3 directly sets the cooling optimum" (EVAL §4) | **wrong** — quadratic idle is 1150× smaller and points the same way |
| "G1 is the blocker" | **still true, and now better motivated** — see below |
| the objective can rank architectures at BB scale | **not yet** — one device compiles the circuit |

### G1 survives, and is now the *only* proposed change that can restore a signal

The cooling pass nulls anything that reaches the gate error through `n̄`. That is what makes
G1 different from every other repair on the list:

```
ε(n̄, N, τ)  =  ε₀  +  Γ·τ(N)  +  κ · (N / ln N) · (2n̄ + 1)
                      └──────────────┬──────────────────┘
                        both survive at n̄ = 0
```

At `n̄ = 0` the chain-length terms are `Γ·τ(N) + κ·N/ln N`, and no amount of cooling removes
them. **G1 is not merely a guard against the optimiser reading R13's cap back as an optimum
— it is the only thing on the table that puts an architecture-dependent quantity into the
gate error that the cooling pass cannot launder into runtime.** G2 (heating from the solved
ion height) reaches the objective only through cooling-stop count → runtime → idle, which
§4 shows is attenuated to a few percent.

## 7 · Caveats

- **`p_eff`'s denominator counts operations, not idle locations.** Same convention as `q01`,
  restated so the 0.7 % threshold comparison can be read with it in view. All 73 pairs sit at
  2.6e-4 … 7.0e-4, i.e. **10–27× below threshold**; none is close.
- **`bb144_mono` is not comparable on `p_eff`.** The shipped Python pipeline emits `CX` as an
  opaque token, so it has 288 one-qubit gates where the OCaml compiler's decomposition has
  3,744. On runtime and device error the two BB144 compilations differ by 2.1 %
  (511.40 ms / 0.14319 against 522.32 ms / 0.14625) — one data point on the inner loop, and
  `bb144_mono` carries no R10 certificate, so it is quoted here and nowhere else.
- **The floor/device split depends on the nine architectures declaring identical scalars.**
  The script asserts this and says so loudly if a future architecture breaks it.
- **`n_ions` varies by device**, so idle error is `n_ions × T_round / T_coh` and not runtime
  alone. Coolant ions are charged idle error like any other.
- The calibration oracle was re-checked and holds: `deck24` replays at **397,184 cost /
  8,808 steps**, EXPECTATIONS MET. No model change was made this session.
