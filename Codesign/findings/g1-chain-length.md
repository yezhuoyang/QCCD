# G1 · The chain-length term, closed — and what it did and did not buy

**Status:** closed. **Verdict:** the free lunch is gone — the one device in the corpus that
actually gates long chains was ranked **4th of 9 and is now last** — but G1 does **not**
restore an architecture signal to today's comparison, because seven of the nine devices
declare trap capacity 2 and the new term is a constant on them. G1 is a **guard on an axis
nobody has swept yet**. It is now safe to sweep it, which is what it was for.

```bash
python Codesign/scripts/g1_chain_length.py --json Codesign/data/g1.json
python -m pytest tests/test_chain_length_gate_error.py -q          # 69 passed
python Compiler/bridge/check_tsir.py Compiler/build/deck24.tsir.json \
    --arch arch/ring144_24v.arch.json --expect-cost 397184 --expect-steps 8808
```

---

## 1 · The closed form, and why nothing in it is fitted

`gate_error(arch, nbar)` was `ε₀ + k·n̄`, with no dependence on how many ions share the trap.
It is now

```
ε(n̄, N)  =  ε₀' + κ · f(N) · (2n̄ + 1)          f(N) = N / ln N     (Murali et al., ISCA 2020)
```

**D1 asked where `κ` comes from and called it "the largest single assumption in the study".
It turns out not to be an assumption at all.** Requiring the new model to equal the old one
at the *reference chain length* — for every `n̄`, not just at one point — pins both constants:

| | | |
|---|---|---|
| match the `n̄` coefficient | `2κ·f(N_ref) = slope` | `κ = 3.4657e-4` |
| match the constant | `ε₀' + κ·f(N_ref) = ε₀` | `ε₀' = ε₀ − slope/2 = 8.40e-4` |

So the **shape** is borrowed from Murali and the **scale** is a consequence of the shipped
calibration. There is no free parameter and nothing to tune.

`N_ref = 2` because `fidelity_at_n0` (0.99816) and `error_vs_quanta` (`linear:2.0e-3`) are
both quoted from **2305.03828**, a two-ion gate zone. That is now declared in each
architecture as `"error_vs_chain": "murali:2"` rather than assumed in code — an architecture
whose gate numbers came from a longer chain must say so.

### The refinement property is exact, not approximate

| n̄ | legacy `ε₀ + k·n̄` | new, at `N_ref` | identical? |
|---|---|---|---|
| 0.0 | 1.840000000000e-03 | 1.840000e-03 | ✅ |
| 0.25 | 2.340000000000e-03 | 2.340000e-03 | ✅ |
| 1.0 | 3.840000000000e-03 | 3.840000e-03 | ✅ |

Bit-exact, on all nine architectures, at every point tested. The first implementation was
*algebraically* equal but drifted by ~1 ULP from the regrouping, and the test caught it. That
distinction is worth a sentence: 1 ULP per gate over 864 gates is a different and weaker claim
than "every already-validated number is unchanged". `gate_error` now returns the reference
expression itself whenever the chain is no longer than the reference.

## 2 · The formula has its own free lunch, and it is refused

`N / ln N` has a minimum at **N = e ≈ 2.718**. Taken literally it says a 3-ion chain is
**5.4 % better** than a 2-ion one:

| N | raw `f(N)/f(2)` | used | ε at n̄ = 0 | ε at R7's cap |
|---:|---:|---:|---:|---:|
| 2 | 1.000 | 1.000 | 1.84e-3 | 3.84e-3 |
| **3** | **0.946** | **1.000** | 1.84e-3 | 3.84e-3 |
| 4 | 1.000 | 1.000 | 1.84e-3 | 3.84e-3 |
| 6 | 1.161 | 1.161 | 2.00e-3 | 4.32e-3 |
| 8 | 1.333 | 1.333 | 2.17e-3 | 4.84e-3 |
| 12 | 1.674 | 1.674 | 2.51e-3 | 5.86e-3 |
| **15** (R13's cap) | **1.920** | **1.920** | **2.76e-3** | **6.60e-3** |

That dip is an artifact of evaluating an asymptotic large-`N` scaling at `N = 3`, not a
measured discount — and handing a search a 5 % discount at `N = 3` is precisely the failure
mode G1 exists to prevent. `_chain_factor` therefore applies a **monotone envelope** from the
reference: a longer chain is never cheaper than the reference, ever. Stated in the code, in
this file, and pinned by a test.

## 3 · What R13's cap now costs

|  | before G1 | after G1 |
|---|---|---|
| a 15-ion chain, `n̄ = 0` | same as 2 ions | **1.50×** |
| a 15-ion chain, at R7's cap | same as 2 ions | **1.72×** |
| the whole feasible band for one gate | `[1.84e-3, 3.84e-3]` = **2.09×** | `[1.84e-3, 6.60e-3]` = **3.59×** |

So G1 widens what geometry can move the gate term by **1.72×**. Against
[`q01b`](q01b-bb144-and-the-floor.md)'s finding that the objective is 96 % floor, this is the
largest single lever added to the study so far — and it is still only 3.59×, not the three
orders of magnitude the literature reports. R7's `max_quanta = 1.0` and the cooling pass
remain the binding constraints.

## 4 · What it did to the 73 verified programs: almost nothing, for a reason worth stating

Chain length at a two-qubit gate, over every verified program:

```
N = 2  →  2258 gates        N = 3  →  62        N = 4  →  23        N = 15  →  28
```

| | |
|---|---|
| programs whose score changed | **2 of 73** |
| unchanged **bit for bit** | 71 |

| circuit | device | max N | `−ln F` was | now | |
|---|---|---:|---:|---:|---:|
| rep9_esm | stationary_chain | 15 | 0.04405 | 0.05834 | **1.325×** |
| ghz16 | stationary_chain | 15 | 0.05547 | 0.07018 | **1.265×** |

63 of the 71 unchanged programs never gate a chain longer than 2 — **seven of the nine
shipped devices declare trap capacity 2**, so on them the new term is a constant and folds
straight back into `ε₀` by construction. The other 8 reach N = 3 or 4, which the monotone
envelope still prices at the reference.

> **This corrects [`q01b`](q01b-bb144-and-the-floor.md) §6.** That file argued G1 is "the only
> change on the list that puts an architecture-dependent quantity into the gate error that the
> cooling pass cannot launder into runtime." The first half is right — `κ·f(N)` is non-zero at
> `n̄ = 0` and no cooling removes it. The implication was too strong: the quantity is only
> *variable* if capacity varies, and today it does not. **G1 does not add signal to the
> current nine-device comparison. It makes capacity a safe axis to add.**

## 5 · The ranking it moved, which is the actual result

`stationary_chain` is the only device in the corpus that gates 15-ion chains. On both circuits
it can run:

| circuit | device | rank before | rank after | |
|---|---|---:|---:|---|
| ghz16 | **stationary_chain** | 4 of 9 | **9 of 9** | its own chains |
| rep9_esm | **stationary_chain** | 4 of 9 | **9 of 9** | its own chains |
| ghz16 / rep9_esm | ladder, chain, dual_loop, racetrack, cyclone_base | 5–9 | 4–8 | displaced by one that did |

**A device that piles 15 ions into a trap was scoring mid-field and is now last.** That is
exactly the artifact G1 was written to remove, caught on the one device in the corpus that
exhibits it. Before G1 the model would have told a search that `stationary_chain` — two traps
of capacity 32 — is a perfectly good machine.

## 6 · A gap this opened, and it is the next thing to look at

**Nothing in the compiler ever chooses a long chain.** `stationary_chain` reaches N = 15
because it has *two traps* and nowhere else to put the ions, not because a placer decided a
long chain was worth it. Every other device is capacity-2, so the question never arises.

So raising trap capacity on, say, the ring may well change **nothing** — the compiler will
keep putting one ion per node and the extra capacity will sit unused. If so, the capacity axis
is inert for a reason that has nothing to do with the error model, and the finding belongs to
CD2's *mapping* axis rather than to CD3's geometry. That is a cheap thing to check and it is
now the next action.

## 7 · Guard rails, all re-checked

| | |
|---|---|
| `deck24` replay | **397,184 cost / 8,808 steps — EXPECTATIONS MET** |
| `bb144_esm` on `ring144_24v` recompiled against the regenerated architecture | 20/20 rules, **R10 `passed`** |
| the 73 verified pairs | 71 bit-identical; the 2 that moved both gate 15-ion chains |
| new tests | `tests/test_chain_length_gate_error.py`, **69 passed** |

## 8 · What was implemented, and one thing deliberately not

- `CorrectedModel.gate_error(arch, nbar, n_chain=None)` — `None` returns the pre-G1 answer, so
  a caller that does not know the chain length is never silently given a different number.
- The replay reads the chain length **the way R13 does** — `occ_before[gate site]` — so the
  error model and the rule that caps it cannot disagree about what a chain is.
- `ReplayResult.chain_len_at_gate`, a histogram rather than a mean, because the mean of a
  distribution that is 99 % twos hides the tail the term is about.
- A bad calibration (`slope > 2·ε₀`, which would send `ε₀'` negative) **raises** rather than
  being clamped to zero.

**Not declared: `us_vs_chain`.** Murali reports gate *duration* scaling with chain size, but
only for **FM** gates — AM scales with ion separation, PM weakly. `ms_gate` cites 2305.03828
for its 25 µs and does not say which it is, so asserting a linear scaling would be inventing a
number rather than refining one. The mechanism is implemented and tested, normalised so the
declared `us` is the duration at `N_ref`; it stays off until an architecture states a gate
type. When it is turned on, the extra elapsed time reaches the error through **R17's anomalous
accrual on its own** — which is also why the `Γ·τ` term of the published formula is *not*
added separately here. It is already in the model, dynamically, and adding it again would
double count.
