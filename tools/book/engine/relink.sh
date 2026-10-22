#!/usr/bin/env bash
# Relink the book's engine (lean.js + lean.wasm) from the runtime-62b6a22 link kit with the exact
# flags of the fork's guarded desktop recipe (docker-wasm/relink-desktop-release.py at
# fb896a60f96a452cbf477aef31dcfbbff3c78f8b = relink-local.sh + -O3 + the canonical export list
# + the versioned dlsym adapter + DECLARE_ASM_MODULE_EXPORTS=0).
#
# usage: relink.sh <kit dir> <recipe dir> <out dir> [extra .o / .a ...] [-- extra emcc flags]
#   <recipe dir> holds emscripten-exports.txt (the canonical list, sha256 1049f282...) and
#   wasm-dlsym.js (sha256 264f0008...); a patched build passes a list with its added exports.
# env EMSDK_DIR = emsdk with 4.0.22 installed and activated.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
KIT=${1:?kit}; RECIPE=${2:?recipe}; OUT=${3:?out}; shift 3
objs=(); flags=()
while [ $# -gt 0 ]; do
  if [ "$1" = "--" ]; then shift; flags=("$@"); break; fi
  objs+=("$1"); shift
done
mkdir -p "$OUT"
start=$(date +%s)
bash "$HERE/emcc.sh" -o "$OUT/lean.js" \
  "$KIT/libleanmain.a" \
  "$KIT/libleanshell.a" \
  "${objs[@]}" \
  -L "$KIT" -lleancpp -lInit -lStd -lLean -lnodefs.js -lleanrt "$KIT/libuv.a" -lstdc++ \
  -sDISABLE_EXCEPTION_CATCHING=0 -pthread \
  -sALLOW_MEMORY_GROWTH=1 \
  -sINCOMING_MODULE_JS_API=print,printErr,setStatus,locateFile,mainScriptUrlOrBlob,INITIAL_MEMORY,wasmMemory,noInitialRun,preRun,onRuntimeInitialized,onAbort,onExit,arguments,ENV \
  -s EXIT_RUNTIME=1 -s MAIN_MODULE=2 -s ERROR_ON_UNDEFINED_SYMBOLS=0 \
  -sEXPORTED_FUNCTIONS=@"$KIT/emscripten-exports.txt" \
  -sEXPORTED_RUNTIME_METHODS=ccall,callMain,FS,ENV,getValue,setValue,stringToNewUTF8,UTF8ToString \
  -s STACK_SIZE=16777216 -sINITIAL_MEMORY=67108864 -sMAXIMUM_MEMORY=4294967296 -sPTHREAD_POOL_SIZE=4 \
  -O3 -sEXPORTED_FUNCTIONS=@"$RECIPE/emscripten-exports.txt" \
  --js-library "$RECIPE/wasm-dlsym.js" \
  -sDECLARE_ASM_MODULE_EXPORTS=0 \
  "${flags[@]}"
echo "linked in $(( $(date +%s) - start )) s"
sha256sum "$OUT/lean.js" "$OUT/lean.wasm"
ls -la "$OUT/lean.js" "$OUT/lean.wasm"
