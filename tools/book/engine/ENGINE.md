# The book's Lean engine

The Lean on these pages runs in your browser: Lean 4.33.0-pre compiled to
WebAssembly, from the fork [cauli/lean4](https://github.com/cauli/lean4) (Apache-2.0) at commit
`62b6a2291302d4bbeace37642a066b7510d0145c`.  No server checks anything.

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
--- a/src/Lean/Shell.lean
+++ b/src/Lean/Shell.lean
@@ -137,12 +137,31 @@
   -- every command, so the final state holds only the last command's messages.
   -- Collect the log after each command instead of reading the end state; seed it
   -- with any header (import) parse errors.
+  --
+  -- That reset also discarded the *parser's* messages: `Frontend.processCommand` puts them
+  -- into `commandState.messages` and then elaborates, so an unclosed paren or a
+  -- `def broken : Nat :=` vanished and the file looked accepted.  So this loop is
+  -- `Frontend.processCommand` inlined, keeping the parse messages of each command.
   let collect : Elab.Frontend.FrontendM MessageLog := do
     let mut acc : MessageLog := headerMessages
     let mut done := false
     while !done do
-      done := (← Elab.Frontend.processCommand)
+      Elab.Frontend.updateCmdPos
+      let cmdState ← Elab.Frontend.getCommandState
+      let ictx ← Elab.Frontend.getInputContext
+      let pstate ← Elab.Frontend.getParserState
+      let scope := cmdState.scopes.head!
+      let pmctx := { env := cmdState.env, options := scope.opts,
+                     currNamespace := scope.currNamespace, openDecls := scope.openDecls }
+      let (cmd, ps, parseMsgs) :=
+        profileit "parsing" scope.opts fun _ => Parser.parseCommand ictx pmctx pstate {}
+      acc := acc ++ parseMsgs
+      modify fun s => { s with commands := s.commands.push cmd }
+      Elab.Frontend.setParserState ps
+      Elab.Frontend.setMessages {}
+      Elab.Frontend.elabCommandAtFrontend cmd
       acc := acc ++ (← Elab.Frontend.getCommandState).messages
+      done := Parser.isTerminalCommand cmd
     return acc
   let (msgLog, _s) ← StateRefT'.run (ReaderT.run collect frontendCtx) frontendState
 
```

The engine's web worker also holds one Emscripten keepalive reference after start-up
(`runtimeKeepalivePush()`), because this build exits its runtime -- closing every file
descriptor and stopping every thread -- the first time it handles a callback with no
reference held, which happened whenever Lean generated equation lemmas (`simp [f]`,
`unfold f`, `termination_by`).  That is a change to the page's worker, not to Lean.

## How it was built

* Link kit: `linkkit-62b6a22.tar.gz` from the release `browser-62b6a22-compact1` of cauli/lean4, sha256
  `1d8345533bd03b8cb72fee36d4ca2c084315414e9561a1d055680af6dc1d2c90`.  It holds the WebAssembly objects of Lean at the commit above.
* Recipe: the fork's guarded desktop relink (`docker-wasm/relink-desktop-release.py` at
  `fb896a60f96a452cbf477aef31dcfbbff3c78f8b`): `relink-local.sh` plus `-O3`, the canonical export list
  (`src/emscripten-exports.txt`, sha256 `1049f282260f3a18e1cf8bfe9b24355a3e515c54a4321d5ab2bd698f1a57a956`), the dlsym adapter
  (`src/wasm-dlsym.js`, sha256 `264f000882cad81e4ed3484a0e3c545d7b533ae9ca06e34041b730afeb699da7`) and `-sDECLARE_ASM_MODULE_EXPORTS=0`.
* Emscripten 4.0.22 (0f3d2e62bccf8e14497ff19e05a1202c51eb0c65).
* Check of the toolchain: relinking the unmodified kit with this recipe reproduced the
  fork's published `lean.js` and `lean.wasm` byte for byte (sha256
  `63aa7b004435f3d90cf19d7c3b3e3284726701ea06da8bc70b015125e1a38b24` and `08d3ae8ee5dec8bae165ad5102e604eca016d74d2303e0b5748690df679a764e`).
* The patched `src/Lean/Shell.lean` was compiled to C by the engine's own Lean (same commit,
  run under Node.js), and the C to a WebAssembly object with the flags the fork's CMake gives
  Lean's generated C under Emscripten:
  `emcc -c -O3 -DNDEBUG -DLEAN_EMSCRIPTEN -sDISABLE_EXCEPTION_CATCHING=0 -pthread -fPIC
  -ffp-contract=off -fdata-sections -ffunction-sections -fvisibility=hidden -DLEAN_EXPORTING`.
  Compiling the *unpatched* file the same way gives an object with the same 650 symbols as
  the kit's, and the same code in 200 of its 201 functions (`lean_shell_main`, the
  command-line driver the pages never call, differs by a few instructions).
* Link: the recipe command with that object (sha256 `e2f27aaf5c44407de7bac8ed669e147c57a2f8aee5fa319855e3d96b936aff36`)
  placed before the archives, so it replaces the kit's `Shell.c.o.export`:

```sh
emcc -o lean.js $KIT/libleanmain.a $KIT/libleanshell.a Shell.c.o.export \
  -L $KIT -lleancpp -lInit -lStd -lLean -lnodefs.js -lleanrt $KIT/libuv.a -lstdc++ \
  -sDISABLE_EXCEPTION_CATCHING=0 -pthread -sALLOW_MEMORY_GROWTH=1 \
  -sINCOMING_MODULE_JS_API=print,printErr,setStatus,locateFile,mainScriptUrlOrBlob,INITIAL_MEMORY,wasmMemory,noInitialRun,preRun,onRuntimeInitialized,onAbort,onExit,arguments,ENV \
  -s EXIT_RUNTIME=1 -s MAIN_MODULE=2 -s ERROR_ON_UNDEFINED_SYMBOLS=0 \
  -sEXPORTED_FUNCTIONS=@$KIT/emscripten-exports.txt \
  -sEXPORTED_RUNTIME_METHODS=ccall,callMain,FS,ENV,getValue,setValue,stringToNewUTF8,UTF8ToString \
  -s STACK_SIZE=16777216 -sINITIAL_MEMORY=67108864 -sMAXIMUM_MEMORY=4294967296 -sPTHREAD_POOL_SIZE=4 \
  -O3 -sEXPORTED_FUNCTIONS=@src/emscripten-exports.txt --js-library src/wasm-dlsym.js \
  -sDECLARE_ASM_MODULE_EXPORTS=0
```

  (`$KIT` is the unpacked link kit; `src/` is the recipe commit's.)  Without
  `Shell.c.o.export` this is the control link above.

The library on these pages (`Init`, the `Lean.Data.Json` closure and the QCCD checker) was
compiled by the fork's unmodified command-line Lean; the patch does not touch that path.  The
checker is a derived copy of the repository's `Compiler/lean` sources (qcheck version
`1c067263bdc9`), made by `tools/book/port_checker.py`.

## Files

| file | bytes | sha256 |
|---|---:|---|
| `init-7e1adc8bda04.index.json` | 93,692 | `170514bf61f1e499b862d857b11d48276e5f6990d4236a559f93a2e74d888132` |
| `init-7e1adc8bda04.pack` | 79,760,876 | `7e1adc8bda042c7f22179d2759efd97434249ccfe9049cfc330d79760bcb3a44` |
| `init-7e1adc8bda04.pack.br` | 25,385,605 | `9fa59ceddcb864035b74a12ee899ce25742a77fa2ccdc195352462e5c5435225` |
| `init-7e1adc8bda04.pack.gz` | 30,877,572 | `6390a9465ef29ae069ba0cde9b144fd403da285d28cf265e7627833c8658383d` |
| `json-050a47e53993.index.json` | 9,046 | `5f3649c42a3c963ac157e60ea7796623ed03e3cfa92be2a4a3392af4e036acbc` |
| `json-050a47e53993.pack` | 24,457,144 | `050a47e5399366fb11aa941e579f4b280f96483a21ba8634df1cbc472654cab3` |
| `json-050a47e53993.pack.br` | 7,997,279 | `4d7be029c984f1def247bf87c24e40322c0fa62e1a3f1a2b5696fd90c32bb643` |
| `json-050a47e53993.pack.gz` | 9,869,146 | `2c372eb0b0b004a95e1ed018f8247ec8855655f0dbbb3cb63836a5ce06d5dcbe` |
| `lean-98b097de1537.js` | 148,402 | `98b097de1537458d3a8305c465929b0b84af06d341cff6aefbc1dcee1d568070` |
| `lean-98b097de1537.js.br` | 35,933 | `cbcf156cb459bcb4b6320182d5905869e5f28fb40eed64729f89b67544b6a6d9` |
| `lean-98b097de1537.js.gz` | 38,452 | `a95c353b21a91a338093a60edccef4677a22e9583d7d4d68b243df6a3dada3e5` |
| `lean-c4e23fca7cc7.wasm` | 100,837,005 | `c4e23fca7cc7599f84a6fa4e56e3fca2ea4eeadbfda11803ef4301779a240b52` |
| `lean-c4e23fca7cc7.wasm.br` | 10,371,131 | `ccf5bdddf2468ec7ab2fa72eac914060f3a0dfffd0038968174a6bf491c2d6ae` |
| `lean-c4e23fca7cc7.wasm.gz` | 17,318,616 | `289204d13ff7691d8ec0186a996f66ee03981215ec1b9a3e17fb79adf65122f0` |
| `manifest.json` | 6,413 | `9ff5f67e8cd0ae5c8d2b86c7071f00e7a12b7492423e46c5b9b648576fce399b` |
| `qccdc-d8b344682447.index.json` | 759 | `763f2ca5915f61ccd243758d4e81de27eae056852a605efc714c746357ec982c` |
| `qccdc-d8b344682447.pack` | 795,788 | `d8b34468244774b92aac884ce8a298a6c9c650f30b05fd04b28bee7c05788aa5` |
| `qccdc-d8b344682447.pack.br` | 260,666 | `53dfab0857461398dcf30997fc471d8885259e36167fd145b26bece0f7712bfd` |
| `qccdc-d8b344682447.pack.gz` | 307,567 | `44326b292705effea837528a11f1ab82eefdd832a47698323f2c81622caa961b` |
