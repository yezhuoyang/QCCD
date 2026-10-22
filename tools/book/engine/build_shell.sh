#!/usr/bin/env bash
# Compile one Lean core module (default Lean/Shell.lean) to C with the engine's own wasm Lean,
# then to a wasm object with the flags the fork's CMake gives Lean-generated C under Emscripten
# (LEANC_EXTRA_CC_FLAGS + LEANC_OPTS + leanc's base flags; -DLEAN_EXPORTING for the `.export`
# objects that libLean.a holds).
#
# usage: build_shell.sh <src root> <out dir> <engine dir> <lean-lib dir> <include dir> [Module/Path]
#   <src root>/Lean/Shell.lean   the (patched or original) source
#   <engine dir>                 bin/lean.js + lib/lean (only lean.js/lean.wasm are used here)
#   <lean-lib dir>               the full Init/Std/Lean library of the same commit (LEAN_PATH)
#   <include dir>                lean/lean.h, lean/config.h, lean/version.h for the commit
# env EMSDK_DIR (for emcc.sh)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -W)  # a Windows path: node is a Windows program
SRC=$(cd "$1" && pwd -W); OUT=$2; ENGINE=$(cd "$3" && pwd -W); LIB=$(cd "$4" && pwd -W); INC=$(cd "$5" && pwd -W); MOD=${6:-Lean/Shell}
mkdir -p "$OUT/work/out/$(dirname "$MOD")"
OUT=$(cd "$OUT" && pwd -W)
rm -rf "$OUT/work/src"; cp -r "$SRC" "$OUT/work/src"
t=$(date +%s)
MSYS_NO_PATHCONV=1 LEAN_NODE_LATE_TASKMGR=4 LEAN_NODE_TIME=1 \
  node --stack-size=8192 "$HERE/../lean-node.cjs" "$ENGINE/bin/lean.js" "$LIB" "$OUT/work" \
  -j0 -R /work/src -o "/work/out/$MOD.olean" -c "/work/out/$MOD.c" --json "/work/src/$MOD.lean" \
  > "$OUT/lean.log" 2>&1 || { echo "lean failed:"; grep -E '"severity":"error"|ABORT|PANIC' "$OUT/lean.log" | head; exit 1; }
echo "lean -c: $(( $(date +%s) - t )) s"
grep -E '"severity":"(error|warning)"' "$OUT/lean.log" | head -5 || true
t=$(date +%s)
bash "$HERE/emcc.sh" -c -O3 -DNDEBUG -DLEAN_EMSCRIPTEN -sDISABLE_EXCEPTION_CATCHING=0 -pthread -fPIC \
  -ffp-contract=off -fdata-sections -ffunction-sections -fvisibility=hidden -DLEAN_EXPORTING \
  -I "$INC" "$OUT/work/out/$MOD.c" -o "$OUT/$(basename "$MOD").c.o.export"
echo "emcc -c: $(( $(date +%s) - t )) s"
ls -la "$OUT/$(basename "$MOD").c.o.export"
