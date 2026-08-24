#!/usr/bin/env bash
# C5 -- the numerical layer.
#
#   1. PLACEMENT: greedy weighted insertion vs a spectral (Fiedler) relaxation, both
#      scored on the true objective, then a hill-climb on the winner.
#   2. THE EXCHANGE RATE: sweep the R7 budget to trace the (runtime, gate error) frontier.
#
# `docs/PLAN.md` §0.2 is why (2) is the interesting one: cost and steps are not rival
# objectives, they are two named halves of ONE error budget.  Transport does not itself
# cause gate error -- it heats, and heating degrades the next gate -- so cooling more
# often buys accuracy with time, and no fixed policy finds the middle of that curve.
set -uo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python}
source ocaml/ocamlenv.sh
(cd ocaml && dune build) || exit 1
EXE=ocaml/_build/default/bin/qccdc_cli.exe

echo "=== 1. placement: which relaxation actually wins? ==="
printf '%-12s %-15s %s\n' circuit arch outcome
for c in ghz8 ghz32 clifford12 qft6; do
  for d in grid9x9 cyclone_base ring144_24v; do
    line=$("$EXE" compile "bench/$c.qasm" --arch "build/$d.expanded.json" \
             -o build/out/_c5 2>&1 | grep -E "candidates|hill-climb" \
             | sed 's/^ *- *//' | tr '\n' ';')
    printf '%-12s %-15s %s\n' "$c" "$d" "$line"
  done
done

echo
echo "=== 2. the (runtime, gate error) frontier ==="
"$EXE" compile bench/clifford12.qasm --arch build/grid9x9.expanded.json \
  -o build/out/c5_prog >/dev/null 2>&1
$PY bridge/c5_pareto.py build/out/c5_prog.tsir.json --arch arch/grid9x9.arch.json \
  --tables qccdsim_jones --budgets 1,2,4,8,16,32,64,none \
  --json build/c5_frontier.json

echo
echo "C5 DONE"
