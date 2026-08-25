# Trap capacity is inert — nothing in the compiler ever fills a trap

**Status:** answered. **Verdict:** raising `ring144_24v`'s trap capacity from 2 to 4 produces a
**byte-identical program** — same 864 gate pairs, same 358.52 ms, and every gate still at chain
length 2. The axis [G1](g1-chain-length.md) was written to guard is currently **unreachable**.
Raising it to 8 makes the device *worse*, for a reason that has nothing to do with capacity.

```bash
# variants: zone_types data/ancilla/trap capacity 2 -> 4, 8; everything else identical
python Compiler/bridge/export_arch.py <variant>.arch.json -o <variant>.expanded.json
Compiler/ocaml/_build/default/bin/qccdc_cli.exe compile Compiler/examples/bb144_esm.qasm \
    --arch <variant>.expanded.json -o bb144_cap<N>
```

---

## 1 · Capacity 4 changes nothing at all

| | pairs | runtime | chain length at every gate |
|---|---:|---:|---|
| capacity 2 (shipped) | 864 | 358.52 ms | `{2: 864}` |
| capacity 4 | 864 | 358.52 ms | `{2: 864}` |

Same batches (546), same rotations (546), same hops (776). **The compiler does not use the
extra room.** The rotation pipeline puts one ion per loop node and docks one at a time; there
is no pass anywhere that would decide to put two ions in a trap and leave a node empty.

So the guard [G1](g1-chain-length.md) installed is correct and currently has nothing to guard.
That is not wasted work — it means the *next* person to write a stacking placer cannot be
misled by a model that prices long chains at zero — but it does move the open question from
CD3 (geometry) to **CD2 · 2 (mapping)**, exactly as `g1` §6 predicted.

## 2 · Capacity 8 is worse, and capacity is not the reason

```
=== capacity 8
  certificate: 929 gate witnesses, self-check OK
  UNREALISED ops: 79 (38,51,76,82,92,112,118,120)
```

79 of 864 contacts unrealised, against a fully compiled program at capacity 2. **More room made
the device compile worse.**

The cause is the fallback gap recorded in [`q01b`](q01b-bb144-and-the-floor.md) §1, now firing
for real. `qccdc_cli.ml` tries rigid rotation only when the general router **raises**
`Route.Unroutable`. At capacity 2 the general router gives up cleanly, rotation takes over, and
the program compiles. At capacity 8 there is enough slack that the general router *doesn't*
give up — it returns normally with a partial placement — so **rotation is never tried** and the
79 ops stay unrealised.

Note what this means for any future sweep: **capacity is not a monotone axis under the current
compiler**, and the non-monotonicity is an artifact of router selection, not of physics. A
sweep run today would report an optimum at capacity 2 and that number would be about
`qccdc_cli.ml`'s control flow.

## 3 · What to fix, in order

1. **Make the rotation fallback fire on a partial placement, not only on an exception.** A
   compile that leaves 79 ops unrealised has declined just as surely as one that raises. This
   is a few lines and it removes the non-monotonicity above.
2. **Then, if capacity is still to be searched, a placer that stacks.** Until one exists the
   axis has no content, and `q03` should be re-run after it does.

## 4 · Caveats

- **Only `ring144_24v`, only `bb144_esm`.** The rotation pipeline is the only router that
  compiles this circuit at all ([`q01b`](q01b-bb144-and-the-floor.md) §1), so this measures the
  rotation pipeline's use of capacity, not every router's.
- **The capacity-4 variant was not rule-checked or R10-verified**, because nothing was claimed
  about its performance — the finding is that its program is *identical* to one that was. If a
  later run quotes a number from a capacity variant, it must go through the full ladder first.
- The variants live in the session scratchpad, not in `arch/`. They are three lines of JSON
  (`zone_types.{data,ancilla,trap}.capacity`) and the command above regenerates them.
