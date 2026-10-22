#!/usr/bin/env python3
"""Write ENGINE.md (the book's engine notes) beside the packs.  No local paths go into it.

usage: python write_engine_md.py --site <site dir> --info <engine-info.json> --patch <unified diff>
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True, type=Path)
    ap.add_argument("--info", required=True, type=Path)
    ap.add_argument("--patch", required=True, type=Path)
    a = ap.parse_args()
    man = json.loads((a.site / "manifest.json").read_text(encoding="utf-8"))
    info = json.loads(a.info.read_text(encoding="utf-8"))
    patch = a.patch.read_text(encoding="utf-8")
    # strip the local file names from the diff header
    lines = patch.splitlines()
    if len(lines) >= 2 and lines[0].startswith("---") and lines[1].startswith("+++"):
        lines[0] = "--- a/src/Lean/Shell.lean"
        lines[1] = "+++ b/src/Lean/Shell.lean"
    patch = "\n".join(lines) + "\n"
    rows = []
    for p in sorted(a.site.iterdir()):
        if p.is_file() and p.name != "ENGINE.md":
            b = p.read_bytes()
            rows.append(f"| `{p.name}` | {len(b):,} | `{hashlib.sha256(b).hexdigest()}` |")
    k, r, e = info["link_kit"], info["recipe"], info["emscripten"]
    d = man["derived_from"]
    text = f"""# The book's Lean engine

The Lean on these pages runs in your browser: Lean {man['engine']['lean']} compiled to
WebAssembly, from the fork [cauli/lean4](https://github.com/cauli/lean4) (Apache-2.0) at commit
`{man['engine']['commit']}`.  No server checks anything.

## What was changed, and why

One function.  `lean_wasm_compile` (`Lean.wasmCompile` in `src/Lean/Shell.lean`), the fork's
entry point for elaborating a source text in a resident environment, lost the parser's messages:
`Command.elabCommandTopLevel` starts each command with an empty message log, and the parser's
messages were put into that log just before.  An unclosed parenthesis, a half-written `exact (`,
or `def broken : Nat :=` therefore produced no error at all, and a page could report such a
file as accepted.  The patch inlines `Frontend.processCommand` in that loop and keeps each
command's parse messages, so they come back as ordinary errors with positions, as the
command-line `lean --json` reports them.  Nothing else in Lean was changed.

```diff
{patch}```

The engine's web worker also holds one Emscripten keepalive reference after start-up
(`runtimeKeepalivePush()`), because this build exits its runtime -- closing every file
descriptor and stopping every thread -- the first time it handles a callback with no
reference held, which happened whenever Lean generated equation lemmas (`simp [f]`,
`unfold f`, `termination_by`).  That is a change to the page's worker, not to Lean.

## How it was built

* Link kit: `{k['file']}` from the release `{k['release']}` of cauli/lean4, sha256
  `{k['sha256']}`.  It holds the WebAssembly objects of Lean at the commit above.
* Recipe: the fork's guarded desktop relink (`docker-wasm/relink-desktop-release.py` at
  `{r['commit']}`): `relink-local.sh` plus `-O3`, the canonical export list
  (`src/emscripten-exports.txt`, sha256 `{r['exports_sha256']}`), the dlsym adapter
  (`src/wasm-dlsym.js`, sha256 `{r['dlsym_sha256']}`) and `-sDECLARE_ASM_MODULE_EXPORTS=0`.
* Emscripten {e}.
* Check of the toolchain: relinking the unmodified kit with this recipe reproduced the
  fork's published `lean.js` and `lean.wasm` byte for byte (sha256
  `{info['control']['lean.js']}` and `{info['control']['lean.wasm']}`).
* The patched `src/Lean/Shell.lean` was compiled to C by the engine's own Lean (same commit,
  run under Node.js), and the C to a WebAssembly object with the flags the fork's CMake gives
  Lean's generated C under Emscripten:
  `emcc -c -O3 -DNDEBUG -DLEAN_EMSCRIPTEN -sDISABLE_EXCEPTION_CATCHING=0 -pthread -fPIC
  -ffp-contract=off -fdata-sections -ffunction-sections -fvisibility=hidden -DLEAN_EXPORTING`.
  Compiling the *unpatched* file the same way gives an object with the same 650 symbols as
  the kit's, and the same code in 200 of its 201 functions (`lean_shell_main`, the
  command-line driver the pages never call, differs by a few instructions).
* Link: the recipe command with that object (sha256 `{info['patched_object_sha256']}`)
  placed before the archives, so it replaces the kit's `Shell.c.o.export`:

```sh
emcc -o lean.js $KIT/libleanmain.a $KIT/libleanshell.a Shell.c.o.export \\
  -L $KIT -lleancpp -lInit -lStd -lLean -lnodefs.js -lleanrt $KIT/libuv.a -lstdc++ \\
  -sDISABLE_EXCEPTION_CATCHING=0 -pthread -sALLOW_MEMORY_GROWTH=1 \\
  -sINCOMING_MODULE_JS_API=print,printErr,setStatus,locateFile,mainScriptUrlOrBlob,INITIAL_MEMORY,wasmMemory,noInitialRun,preRun,onRuntimeInitialized,onAbort,onExit,arguments,ENV \\
  -s EXIT_RUNTIME=1 -s MAIN_MODULE=2 -s ERROR_ON_UNDEFINED_SYMBOLS=0 \\
  -sEXPORTED_FUNCTIONS=@$KIT/emscripten-exports.txt \\
  -sEXPORTED_RUNTIME_METHODS=ccall,callMain,FS,ENV,getValue,setValue,stringToNewUTF8,UTF8ToString \\
  -s STACK_SIZE=16777216 -sINITIAL_MEMORY=67108864 -sMAXIMUM_MEMORY=4294967296 -sPTHREAD_POOL_SIZE=4 \\
  -O3 -sEXPORTED_FUNCTIONS=@src/emscripten-exports.txt --js-library src/wasm-dlsym.js \\
  -sDECLARE_ASM_MODULE_EXPORTS=0
```

  (`$KIT` is the unpacked link kit; `src/` is the recipe commit's.)  Without
  `Shell.c.o.export` this is the control link above.

The library on these pages (`Init`, the `Lean.Data.Json` closure and the QCCD checker) was
compiled by the fork's unmodified command-line Lean; the patch does not touch that path.  The
checker is a derived copy of the repository's `Compiler/lean` sources (qcheck version
`{d['qcheck_version']}`), made by `tools/book/port_checker.py`.

## Files

| file | bytes | sha256 |
|---|---:|---|
""" + "\n".join(rows) + "\n"
    (a.site / "ENGINE.md").write_text(text, encoding="utf-8")
    print(f"wrote {a.site / 'ENGINE.md'} ({len(rows)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
