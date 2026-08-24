#!/usr/bin/env bash
# Everything, in order.  One command to establish that the compiler works.
#
#   C0  the OCaml/Python interface, against the 397 184 / 8 808 oracle
#   C1  the QASM front end, against qiskit's circuit_to_dag
#   C2  the native gate set, against Lean and against the defining unitaries
#   C3  end-to-end compilation, against all 23 rules
#   C6  the proved checker, and R10
#   --  the verification MATRIX: every example on every architecture
#   C7  where the general router's range ends, and why
#
# C4 (the SAT oracle) and C5 (the frontier) are measurements rather than gates; they take
# minutes and are run separately by `run_c4.sh` and `run_c5.sh`.
set -uo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python}
export PYTHONIOENCODING=utf-8
fail=0

banner () { echo; echo "=============================================================="; echo "$1"; echo "=============================================================="; }

banner "C0-C2: interface, front end, gate set"
for g in run_c0.sh run_c1.sh run_c2.sh; do
  out=$(bash "$g" 2>&1)
  verdict=$(echo "$out" | grep -oE "C[0-9] (PASS|FAIL)" | tail -1)
  echo "  $g -> ${verdict:-NO VERDICT}"
  echo "$verdict" | grep -q PASS || { fail=1; echo "$out" | tail -15; }
done

banner "C3 + C6: compilation, the 23 rules, and R10"
out=$(bash run_c3.sh 2>&1); echo "$out" | tail -6
echo "$out" | grep -q "C3 PASS" || fail=1
out=$(bash run_c6.sh 2>&1); echo "$out" | grep -E "MUTATION|R10 passed on"
echo "$out" | grep -q "MUTATION PASS" || fail=1

banner "A bad program must be REJECTED"
# The conflicts the hardware actually has -- trap capacity, junction occupancy, segment
# sharing, head-on swaps, gate zones, heating, intra-trap parallelism -- injected one at a
# time into a program the verifier has already accepted.
for d in grid9x9 ring144_24v; do
  $PY bridge/mutate_program.py "build/out/st_$d.cooled.tsir.json"     --arch "arch/$d.arch.json" 2>/dev/null | tail -2 | sed "s/^/  [$d] /"
done

banner "The verification matrix: every example on every architecture"
$PY bridge/gen_examples.py -o examples >/dev/null 2>&1
$PY bridge/run_matrix.py --json build/matrix.json || fail=1

banner "Animations"
# --qasm adds the CIRCUIT pane: the source statement each instruction is discharging,
# stepped with the hardware.  The join is checked against the certificate before it is
# drawn, so a page that renders at all is one whose attribution agrees with the witnesses
# the Lean checker decided.
for pair in steane_esm:grid9x9 surface17_esm:ring144_24v ghz16:cyclone_base; do
  c=${pair%%:*}; d=${pair##*:}
  $PY bridge/render.py "build/out/${c}_$d.cooled.tsir.json" --arch "arch/$d.arch.json" \
      --qasm "examples/$c.qasm" -o "../out/compiled/${c}_$d.html" 2>&1 | tail -2
done

banner "C7: where the general router's range ends, and rigid rotation past it"
$PY bridge/c7_occupancy.py --device ring144_24v --json build/c7_ring.json

echo
echo "-- BB[[144,12,12]] on ring144_24v, which the individual-ion router cannot reach --"
QC=ocaml/_build/default/bin/qccdc_cli.exe
$QC rotate build/bb144_esm.qasm --arch build/ring144_24v.expanded.json \
    -o build/out/bb144_rot 2>&1 | tail -3 || fail=1
$PY bridge/insert_cooling.py build/out/bb144_rot.tsir.json \
    --arch arch/ring144_24v.arch.json -o build/out/bb144_rot.cooled.tsir.json 2>&1 | tail -1
$PY bridge/check_tsir.py build/out/bb144_rot.cooled.tsir.json \
    --arch arch/ring144_24v.arch.json 2>&1 | tail -3 || fail=1
$PY bridge/check_cert.py build/out/bb144_rot --qasm build/bb144_esm.qasm \
    --arch arch/ring144_24v.arch.json 2>&1 | tail -4
echo "  (the proved Lean checker accepts this one too, in ~5 min -- it is not run here:"
echo "     python bridge/mk_qcheck_input.py build/out/bb144_rot \\"
echo "         --arch build/ring144_24v.expanded.json -o build/qc_bb144.json"
echo "     python bridge/check_cert.py build/out/bb144_rot --qasm build/bb144_esm.qasm \\"
echo "         --arch arch/ring144_24v.arch.json --qcheck build/qc_bb144.json )"

banner "verdict"
if [ "$fail" -eq 0 ]; then echo "ALL GATES PASS"; else echo "SOMETHING FAILED"; fi
exit "$fail"
