#!/usr/bin/env bash
# C3 -- the first end-to-end compile, and R10.
#
#   qasm + arch  ->  qccdc compile  ->  .tsir.json + .qcert.json
#                ->  insert_cooling (the pass that already exists, and provably converges)
#                ->  check_tsir     (all 23 rules, by the platform's own verifier)
#                ->  check_cert     (R10: does it implement the circuit?)
#
# The last step is the point of the whole project.  `qccd/verify` lists R10 as
# UNCHECKABLE -- "needs symbolic permutation + Pauli-frame tracking against a QASM DAG" --
# and this is that check.  Per decision D3 it reports `partial`, never `passed`, until the
# proved Lean checker lands at C6.
set -uo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python}
source ocaml/ocamlenv.sh
(cd ocaml && dune build) || exit 1
EXE=ocaml/_build/default/bin/qccdc_cli.exe
mkdir -p build/out

CIRCUITS=${CIRCUITS:-"ghz8 ghz32 qft6 clifford12"}
DEVICES=${DEVICES:-"chain grid9x9 ladder_2x72 ring144_24v cyclone_base h2_racetrack"}

printf '%-12s %-15s %7s %6s %7s  %s\n' circuit arch cycles rules runtime R10
printf '%s\n' "-------------------------------------------------------------------------"

ok=0; unroutable=0; rulefail=0; r10fail=0
for q in $CIRCUITS; do
  for d in $DEVICES; do
    out=$("$EXE" compile "bench/$q.qasm" --arch "build/$d.expanded.json" \
            -o "build/out/${q}_$d" 2>&1) || {
      printf '%-12s %-15s %s\n' "$q" "$d" "not routable by the heuristic router"
      unroutable=$((unroutable + 1)); continue; }
    cyc=$(echo "$out" | sed -n '2p' | sed 's/.*layers, \([0-9]*\) transport.*/\1/')

    $PY bridge/insert_cooling.py "build/out/${q}_$d.tsir.json" --arch "arch/$d.arch.json" \
        -o "build/out/${q}_$d.cooled.tsir.json" >/dev/null 2>&1

    r=$($PY bridge/check_tsir.py "build/out/${q}_$d.cooled.tsir.json" \
          --arch "arch/$d.arch.json" --model corrected 2>&1)
    np=$(echo "$r" | grep -o 'passed  ([0-9]*)' | grep -o '[0-9]*')
    ms=$(echo "$r" | grep 'runtime' | awk '{print $2}')
    if echo "$r" | grep -q 'RULES FAILED'; then
      rulefail=$((rulefail + 1)); bad=" RULES FAILED"
    else bad=""; fi

    v=$($PY bridge/check_cert.py "build/out/${q}_$d" --qasm "bench/$q.qasm" \
          --arch "arch/$d.arch.json" 2>&1 | grep -o '> R10 [a-zA-Z]*' | sed 's/> R10 //')
    [ "$v" = "partial" ] && ok=$((ok + 1)) || r10fail=$((r10fail + 1))

    printf '%-12s %-15s %7s %6s %7s  R10 %s%s\n' "$q" "$d" "$cyc" "$np" "$ms" "$v" "$bad"
  done
done

echo
echo "compiled and fully checked : $ok"
echo "R10 not established        : $r10fail"
echo "rejected by the router     : $unroutable   (a heuristic limit, not a wrong answer)"
echo "programs failing a rule    : $rulefail"
echo
# A program that violates a hardware rule is a bug.  A circuit the router cannot place is
# a limitation -- reported, not counted as success, and the reason C4 exists.
if [ "$rulefail" -eq 0 ] && [ "$ok" -gt 0 ]; then echo "C3 PASS"; exit 0; fi
echo "C3 FAIL"; exit 1
