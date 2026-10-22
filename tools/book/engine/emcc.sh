#!/usr/bin/env bash
# emcc from a local emsdk on Windows (Git Bash): emsdk ships only .bat/.ps1 launchers there.
# usage: EMSDK_DIR=<emsdk checkout with 4.0.22 installed and activated> emcc.sh [emcc args...]
set -euo pipefail
E="${EMSDK_DIR:?set EMSDK_DIR to the emsdk directory}"
export EMSDK="$E" EM_CONFIG="$E/.emscripten"
PY=$(ls -d "$E"/python/*/python.exe | head -1)
NODE=$(ls -d "$E"/node/*/node.exe | head -1)
export EMSDK_PYTHON="$PY" EMSDK_NODE="$NODE"
# the guarded recipe gives the JS optimizer a large Node heap
export NODE_OPTIONS="${NODE_OPTIONS:---max-old-space-size=8192}"
exec "$PY" "$E/upstream/emscripten/emcc.py" "$@"
