# Activate the opam `default` switch (OCaml 5.3.0) for MSYS git-bash.
# `ocaml` is not on PATH by default on this machine -- the same trap
# LeanQEC/verifier-ml/ocamlenv.sh exists to solve. Source this, do not export globally.
export PATH="/c/Users/yezhu/AppData/Local/opam/default/bin:$PATH"
export OPAM_SWITCH_PREFIX='C:\Users\yezhu\AppData\Local\opam\default'
export CAML_LD_LIBRARY_PATH='C:\Users\yezhu\AppData\Local\opam\default\lib\stublibs;C:\Users\yezhu\AppData\Local\opam\default\lib\ocaml\stublibs;C:\Users\yezhu\AppData\Local\opam\default\lib\ocaml'
