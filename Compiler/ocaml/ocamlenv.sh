# Activate the opam switch for MSYS git-bash, macOS or Linux: the same as ../ocamlenv.sh, which
# is the one the build scripts source.  This copy used to spell out one machine's opam folder.
# Source this, do not export globally.
. "$(dirname "${BASH_SOURCE[0]:-$0}")/../ocamlenv.sh"
