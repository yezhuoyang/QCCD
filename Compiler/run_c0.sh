#!/usr/bin/env bash
# C0 -- prove the OCaml/Python interface before building a compiler on it.
#
# The loop this closes:
#
#   Python  export_arch.py     arch/*.arch.json  ->  build/*.expanded.json
#   Python  export_deck.py     the shipped schedule  ->  build/deck24.tsir.json
#   OCaml   qccdc arch         reads every expanded architecture
#   OCaml   qccdc roundtrip    reads and rewrites every TSIR fixture
#   Python  diff_tsir.py       every field preserved, in order
#   Python  check_tsir.py      the OCaml-written deck schedule still replays to
#                              397 184 cost / 8 808 steps with every rule passing
#
# The last step is the one that matters.  A round-trip that preserves the document but
# changes what the hardware would do is not a round-trip, and only the replay can tell
# the difference.
set -euo pipefail
cd "$(dirname "$0")"

ROOT=..
BUILD=build
FIX=$BUILD/fixtures
PY=${PYTHON:-python}

source ocaml/ocamlenv.sh

echo "=== 1. expand every architecture ==="
mkdir -p "$BUILD"
for f in "$ROOT"/arch/*.arch.json; do
  n=$(basename "$f" .arch.json)
  out=$($PY bridge/export_arch.py "$f" -o "$BUILD/$n.expanded.json")
  echo "$out" | sed -n 1p
done

echo
echo "=== 2. export the fixtures ==="
out=$($PY bridge/export_deck.py -o "$BUILD/deck24.tsir.json");   echo "$out" | sed -n 1p
out=$($PY bridge/export_programs.py -o "$FIX");                  echo "$out" | sed -n '$p'
out=$($PY bridge/export_shapes.py -o "$FIX/shapes.tsir.json");   echo "$out" | sed -n 1p

echo
echo "=== 3. OCaml reads every architecture ==="
(cd ocaml && dune build)
# NOTE: capture, do not pipe into `head`.  `head -1` closes the pipe after one line and
# the OCaml writer then dies with Sys_error("Invalid argument") on Windows -- a flake
# that looks exactly like a parse failure and is not one.
for f in "$BUILD"/*.expanded.json; do
  out=$(cd ocaml && dune exec --no-build bin/qccdc_cli.exe -- arch "../$f") || exit 3
  echo "$out" | sed -n 1p
done

echo
echo "=== 4. OCaml round-trips every program, and every field survives ==="
fail=0
for f in "$BUILD/deck24.tsir.json" "$FIX"/*.tsir.json; do
  [ -e "$f" ] || continue
  stem=$(basename "$f" .tsir.json)
  out="$BUILD/rt_$stem.tsir.json"
  (cd ocaml && dune exec --no-build bin/qccdc_cli.exe -- roundtrip "../$f" -o "../$out") >/dev/null
  d=$($PY bridge/diff_tsir.py "$f" "$out" || true)
  if echo "$d" | grep -q IDENTICAL; then
    printf '  %-24s IDENTICAL\n' "$stem"
  else
    printf '  %-24s DIFFERS\n' "$stem"
    echo "$d" | sed -n '1,8p'
    fail=1
  fi
done

echo
echo "=== 5. the OCaml-written deck schedule still hits the oracle ==="
$PY bridge/check_tsir.py "$BUILD/rt_deck24.tsir.json" \
  --arch arch/ring144_24v.arch.json --model deck \
  --expect-cost 397184 --expect-steps 8808 || fail=1

echo
if [ "$fail" -eq 0 ]; then
  echo "C0 PASS"
else
  echo "C0 FAIL"
fi
exit $fail
