# Put OCaml 5.3 (dune, yojson) on PATH for the scripts that build the compiler.  Source this,
# do not export globally.
#   macOS / Linux: the opam switch in $QCCD_OPAM_SWITCH, else opam's current one
#     (brew install opam; opam init; opam switch create 5.3.0; opam install dune yojson.2.2.2)
#   Windows (MSYS git-bash): the opam `default` switch, where `ocaml` is not on PATH by default --
#     the same trap LeanQEC/verifier-ml/ocamlenv.sh exists to solve.
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    _opam="C:\\Users\\$USERNAME\\AppData\\Local\\opam\\default"
    export PATH="/c/Users/$USERNAME/AppData/Local/opam/default/bin:$PATH"
    export OPAM_SWITCH_PREFIX="$_opam"
    export CAML_LD_LIBRARY_PATH="$_opam\\lib\\stublibs;$_opam\\lib\\ocaml\\stublibs;$_opam\\lib\\ocaml"
    ;;
  *)
    if command -v opam >/dev/null 2>&1; then
      eval "$(opam env ${QCCD_OPAM_SWITCH:+--switch="$QCCD_OPAM_SWITCH"} --set-switch 2>/dev/null)"
    fi
    ;;
esac
