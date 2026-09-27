# Sets PY to a Python the toolchain can run (3.10 or newer): $PYTHON if set, else the first of
# python, python3, python3.14 ... python3.10 on PATH that is new enough.  On a Mac `python` does
# not exist and /usr/bin/python3 is 3.9, which fails on the first import.  Source this.
if [ -z "${PYTHON:-}" ]; then
  for _c in python python3 python3.14 python3.13 python3.12 python3.11 python3.10; do
    _p=$(command -v "$_c" 2>/dev/null) || continue
    if "$_p" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then PYTHON=$_p; break; fi
  done
fi
PY=${PYTHON:?no Python 3.10 or newer on PATH: activate the venv, or set PYTHON}
