#!/usr/bin/env bash
# C4 -- the SAT optimality oracle, and the price of broadcast control.
#
# Two questions, answered on the routing sub-problems the compiler actually solved:
#
#   1. how far from optimal is the heuristic router?
#   2. what does one-waveform-per-loop control cost, in cycles?
#
# `Compiler/PLAN.md` is explicit that a heuristic with no oracle produces unfalsifiable
# numbers.  This is the oracle.  Every schedule it returns is re-checked against the
# constraints independently of the encoder, because an encoding written too weakly would
# make the optimum look smaller and the heuristic look worse than it is.
#
# The direct-wired device is the CONTROL: it has no named loops, so constraint 3 cannot
# bind there and the measured price must come out zero.  If it ever does not, the
# measurement is wrong.
set -uo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python}
source ocaml/ocamlenv.sh
(cd ocaml && dune build) || exit 1
EXE=ocaml/_build/default/bin/qccdc_cli.exe

MAX=${MAX:-8}
CAP=${CAP:-12}

# (circuit, device) pairs whose instances fit the exact tier.  `ring144_24v` does not:
# its sub-problems have heuristic makespans of 20-56 cycles, well past what a monolithic
# encoding reaches, which is the tier boundary PLAN 6 predicted rather than a surprise.
PAIRS="qft6:h2_racetrack clifford12:cyclone_base clifford12:grid9x9"

for pair in $PAIRS; do
  c=${pair%%:*}; d=${pair##*:}
  inst="build/inst_${c}_${d}.json"
  "$EXE" route-instances "bench/$c.qasm" --arch "build/$d.expanded.json" -o "$inst" \
    >/dev/null 2>&1 || { echo "$c on $d: not routable"; continue; }
  echo "=============================================================="
  $PY bridge/c4_gap.py "$inst" --max "$MAX" --cap "$CAP" --timeout 20
  echo
done

echo "=============================================================="
echo "The direct-wired control (grid9x9) must show a broadcast price of 0."
echo "C4 DONE"
