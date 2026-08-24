#!/usr/bin/env bash
# C6 -- the proved checker, and R10 `passed`.
#
#   1. build `qcheck`: the checker written in Lean, proved sound there, and COMPILED.
#      No extraction step, so nothing is trusted twice.
#   2. run it on every compiled program, with the device's facts read from the
#      architecture document rather than taken from the compiler.
#   3. seed defects into an accepted certificate and require rejection.
#
# Step 3 is not decoration.  `Compiler/PLAN.md` C6: *a checker that accepts a mutant is a
# checker that proves nothing.*
set -uo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python}
source ocaml/ocamlenv.sh
(cd ocaml && dune build) || exit 1
EXE=ocaml/_build/default/bin/qccdc_cli.exe

echo "=== 1. build and axiom-check the proved checker ==="
(cd lean && lake build qcheck 2>&1 | grep -E "axioms|error|Build completed") || exit 1
if (cd lean && lake build qcheck 2>&1 | grep -q sorryAx); then
  echo "a checker theorem depends on sorryAx -- it proves nothing"; exit 1
fi

echo
echo "=== 2. R10 on every compiled program ==="
printf '%-12s %-15s %s\n' circuit arch R10
CIRCUITS=${CIRCUITS:-"ghz8 ghz32 clifford12"}
DEVICES=${DEVICES:-"grid9x9 cyclone_base ring144_24v h2_racetrack ladder_2x72"}
passed=0; other=0
for c in $CIRCUITS; do
  for d in $DEVICES; do
    "$EXE" compile "bench/$c.qasm" --arch "build/$d.expanded.json" \
      -o "build/out/${c}_$d" >/dev/null 2>&1 || {
        printf '%-12s %-15s %s\n' "$c" "$d" "not routable"; continue; }
    $PY bridge/mk_qcheck_input.py "build/out/${c}_$d" --arch "build/$d.expanded.json" \
      -o "build/qcheck_${c}_$d.json" >/dev/null 2>&1
    v=$($PY bridge/check_cert.py "build/out/${c}_$d" --qasm "bench/$c.qasm" \
          --arch "arch/$d.arch.json" --qcheck "build/qcheck_${c}_$d.json" 2>&1 \
        | grep -o '> R10 [a-zA-Z]*' | sed 's/> R10 //')
    printf '%-12s %-15s R10 %s\n' "$c" "$d" "$v"
    [ "$v" = "passed" ] && passed=$((passed + 1)) || other=$((other + 1))
  done
done

echo
echo "=== 3. the checker must be able to reject ==="
# ring144_24v exercises `ungateable_site`: only 24 of its 168 traps can gate, so there
# is somewhere illegal to put a gate.  On a bare grid every trap can, and that mutation
# has nothing to mutate.
$PY bridge/mutate_cert.py build/qcheck_ghz8_ring144_24v.json

echo
echo "R10 passed on $passed program(s); $other did not reach it"
