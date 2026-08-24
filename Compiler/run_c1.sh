#!/usr/bin/env bash
# C1 -- the QASM front end, differential-tested against qiskit's `circuit_to_dag`.
#
#   1. build the OCaml front end
#   2. generate the corpus: 7 named circuits + 500 seeded random ones
#   3. parse every one with BOTH front ends and compare circuit + DAG
#   4. prove the comparator can fail, by mutating our side seven ways
#
# Step 4 is not ceremony.  Step 3 passing means nothing unless a wrong answer would have
# been reported, and one of the mutations (`barrier_split`) is invisible to any
# comparator that checks op counts and edge counts but not per-wire order.
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python}
source ocaml/ocamlenv.sh

echo "=== 1. build ==="
(cd ocaml && dune build)
echo "  ok"

echo
echo "=== 2. generate the corpus ==="
$PY bridge/gen_bench.py -o bench --random "${RANDOM_N:-500}" --seed "${SEED:-7}"

echo
echo "=== 3. differential against qiskit ==="
$PY bridge/run_c1.py --bench bench

echo
echo "=== 4. the comparator must be able to fail ==="
$PY bridge/mutate_c1.py --bench bench
