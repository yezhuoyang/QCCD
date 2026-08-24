#!/usr/bin/env bash
# C2 -- the native gate set, and the Lean theorems it implements.
#
#   1. derive the decompositions numerically  (the SEARCH; not trusted)
#   2. prove them in Lean, and print what they depend on  (the PROOF; trusted)
#   3. differential-test the OCaml table against the defining unitaries
#   4. decompose the whole C1 corpus, and refuse to pass if any gate is unsupported
#
# Step 2's `#print axioms` is the part that matters.  A theorem that reaches for `sorryAx`
# proves nothing, and the only way to know is to ask.
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python}
export PYTHONIOENCODING=utf-8
source ocaml/ocamlenv.sh

echo "=== 1. derive the identities numerically (untrusted search) ==="
$PY bridge/derive_pulses.py -o build/pulses.json | tail -6

echo
echo "=== 2. prove them in Lean ==="
(cd lean && lake build 2>&1 | grep -E "axioms|error|Build completed") || {
  echo "LEAN BUILD FAILED"; exit 1; }
if (cd lean && lake build 2>&1 | grep -q sorryAx); then
  echo "A THEOREM DEPENDS ON sorryAx -- it proves nothing"; exit 1
fi

echo
echo "=== 3. the OCaml table vs the defining unitaries ==="
(cd ocaml && dune build)
(cd ocaml && dune exec --no-build bin/qccdc_cli.exe -- pulses-selftest)

echo
echo "=== 4. every circuit in the corpus decomposes ==="
EXE=ocaml/_build/default/bin/qccdc_cli.exe
bad=0; n=0
for f in bench/*.qasm bench/parse_only/*.qasm bench/random/*.qasm; do
  [ -e "$f" ] || continue
  n=$((n + 1))
  if ! out=$("$EXE" decompose "$f" 2>&1) || echo "$out" | grep -q UNSUPPORTED; then
    bad=$((bad + 1))
    [ "$bad" -le 3 ] && { echo "  $f"; echo "$out" | grep UNSUPPORTED || true; }
  fi
done
echo "  $((n - bad))/$n circuits fully decomposed"

echo
if [ "$bad" -eq 0 ]; then echo "C2 PASS"; else echo "C2 FAIL"; fi
exit "$bad"
