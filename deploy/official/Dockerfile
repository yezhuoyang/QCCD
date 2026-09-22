# The QCCD reference evaluator + official submission service, as one version-pinned image.
#
# Three stages build the trusted toolchain once; the final image carries only what grading
# and serving need.  The same image runs three roles (see docker-compose.yml):
#   api      python -m qccd.official.service serve --host 0.0.0.0
#   worker   python -m qccd.official.worker worker     (database <-> spool; never runs the toolchain)
#   grader   python -m qccd.official.worker grader     (spool -> reports; no network, no secrets)
#
# STATUS: written against the repository's pinned versions (OCaml 5.3.0 + dune + yojson 2.2.2,
# Lean v4.29.0-rc2, Python 3.14, stim 1.15.0, qiskit 2.3.1, numpy 2.4.4).  It has NOT been
# built on the development machine (the Docker daemon was not running); see README.md.

# ---- 1. the OCaml compiler (qccdc_cli) -------------------------------------------------
FROM ocaml/opam:debian-12-ocaml-5.3 AS ocaml
RUN opam install -y dune yojson.2.2.2
WORKDIR /src
COPY --chown=opam Compiler/ocaml/ ./
RUN rm -rf _build && opam exec -- dune build bin/qccdc_cli.exe

# ---- 2. the proved Lean checker (qcheck) -----------------------------------------------
# qcheck imports only Lean core and QCCDC.Cert.*, so it is built WITHOUT Mathlib from a
# minimal lakefile: the checker's sources, byte for byte, and nothing else.
FROM debian:12-slim AS lean
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates git \
 && rm -rf /var/lib/apt/lists/*
RUN curl -sSfL https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh \
    | sh -s -- -y --default-toolchain none
ENV PATH="/root/.elan/bin:${PATH}"
WORKDIR /src
COPY Compiler/lean/lean-toolchain Compiler/lean/Main.lean ./
COPY Compiler/lean/QCCDC/Cert ./QCCDC/Cert
RUN printf 'name = "qcheck_only"\ndefaultTargets = ["qcheck"]\n\n[[lean_lib]]\nname = "QCCDC"\nglobs = ["QCCDC.Cert.+"]\n\n[[lean_exe]]\nname = "qcheck"\nroot = "Main"\n' > lakefile.toml \
 && lake build qcheck

# ---- 3. the runtime ----------------------------------------------------------------------
FROM python:3.14-slim
RUN useradd --create-home --uid 10001 qccd
RUN pip install --no-cache-dir fastapi==0.135.3 starlette==1.0.0 uvicorn==0.43.0 pydantic==2.13.5 \
    "psycopg[binary]>=3.2,<3.3" stim==1.15.0 qiskit==2.3.1 numpy==2.4.4
WORKDIR /opt/qccd
COPY qccd/ ./qccd/
COPY arch/ ./arch/
COPY Compiler/bridge/ ./Compiler/bridge/
COPY --from=ocaml /src/_build/default/bin/qccdc_cli.exe ./Compiler/ocaml/_build/default/bin/qccdc_cli
COPY --from=lean /src/.lake/build/bin/qcheck ./Compiler/lean/.lake/build/bin/qcheck
ENV PYTHONPATH=/opt/qccd \
    QCCD_QCCDC=/opt/qccd/Compiler/ocaml/_build/default/bin/qccdc_cli \
    QCCD_QCHECK=/opt/qccd/Compiler/lean/.lake/build/bin/qcheck \
    QCCD_OFFICIAL_RELEASES=/opt/qccd/qccd/workspace/releases \
    PYTHONDONTWRITEBYTECODE=1
USER qccd
# the evaluator identity recorded in every report includes the digests of these binaries
CMD ["python", "-m", "qccd.official.service", "serve", "--host", "0.0.0.0", "--port", "8300"]
