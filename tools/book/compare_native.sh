#!/usr/bin/env bash
# Differential test: the repo's native qcheck vs a qcheck built from the derived sources,
# on every *.qcheck.json in the repository (full stdout compared, plus exit codes).
# usage: compare_native.sh <repo> <derived qcheck.exe> [<repo qcheck.exe>]
set -u
repo="$1"; port="$2"; base="${3:-$repo/Compiler/lean/.lake/build/bin/qcheck.exe}"
cd "$repo" || exit 2
same=0; diff=0; acc=0; rej=0; ta=0; tb=0; n=0
while IFS= read -r f; do
  n=$((n+1))
  s=$(date +%s%N); oa=$("$base" "$f" 2>&1); ca=$?; m=$(date +%s%N); ob=$("$port" "$f" 2>&1); cb=$?; e=$(date +%s%N)
  ta=$((ta+(m-s)/1000000)); tb=$((tb+(e-m)/1000000))
  if [ "$oa" = "$ob" ] && [ $ca = $cb ]; then same=$((same+1)); else diff=$((diff+1)); echo "DIFF $f (exit $ca vs $cb)"; fi
  case "$oa" in *ACCEPTED*) acc=$((acc+1));; *REJECTED*) rej=$((rej+1));; esac
done < <(find . -name "*.qcheck.json" -not -path "*/node_modules/*" | sort)
echo "inputs=$n identical=$same differ=$diff accepted=$acc rejected=$rej total_ms base=$ta derived=$tb"
