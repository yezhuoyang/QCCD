# The prebuilt compiler and checker (`qccd toolchain install`)

A fresh clone has the compiler's OCaml source but no binary. `qccd toolchain install`
(`qccd/workspace/toolchain.py`) downloads a prebuilt `qccdc_cli` for Windows or Linux on x86-64
or macOS on Apple silicon from `https://qccd.academy/downloads/toolchain/qccdc/<version>/`, and
the proved Lean checker `qcheck` (macOS on Apple silicon so far) from `.../toolchain/qcheck/<version>/`.
It refuses each file unless its size and SHA-256 match `qccd/workspace/toolchain.json`, installs
it under `~/.qccd/toolchain/`, and runs it once: the compiler on a Bell circuit, the checker on a
certificate it must accept and one it must reject. An Intel Mac builds from source.

The macOS files are served gzipped (`"encoding": "gzip"`): `qcheck` carries the Lean runtime and
is 106 MB, 37 MB compressed. `bytes`/`sha256` pin the download, `unpacked_bytes`/`unpacked_sha256`
what it unpacks to; both are checked before anything runs.

`<version>` is the first 12 hex digits of the git tree of `Compiler/ocaml` the binaries were
built from. `qcheck`'s is `toolchain.qcheck_version` over the git object ids of the paths in
`toolchain.QCHECK_SOURCES` (`Main.lean`, `QCCDC/Cert`, `lean-toolchain`, `checker/lakefile.toml`):
a change to the Pulse proofs beside them does not stale it. `tests/test_workspace_toolchain.py`
fails when a commit changes either tool's sources without a new release, because installed
binaries would then be older than the source the Python side expects.

## Making a release

Build from `git archive`, never from a working tree: other sessions' uncommitted edits to
`Compiler/ocaml` must not reach a published binary.

```bash
C=$(git rev-parse HEAD); T=$(git rev-parse HEAD:Compiler/ocaml); V=${T:0:12}
mkdir -p /tmp/tc/src && git archive $C Compiler/ocaml | tar -x -C /tmp/tc/src

# Windows x86-64, on Windows: the opam `default` switch (OCaml 5.3.0, yojson 2.2.2)
(cd /tmp/tc/src/Compiler/ocaml && source ./ocamlenv.sh && rm -rf _build && dune build bin/qccdc_cli.exe)

# Linux x86-64, with Docker
docker build -f deploy/toolchain/Dockerfile.linux -o type=local,dest=/tmp/tc/linux /tmp/tc/src

mkdir -p /tmp/tc/pub/$V
cp /tmp/tc/src/Compiler/ocaml/_build/default/bin/qccdc_cli.exe /tmp/tc/pub/$V/qccdc_cli-windows-x86_64.exe
cp /tmp/tc/linux/qccdc_cli /tmp/tc/pub/$V/qccdc_cli-linux-x86_64
(cd /tmp/tc/pub/$V && sha256sum qccdc_cli-* > SHA256SUMS)
```

macOS on Apple silicon, on a Mac. Build in a switch created with the deployment target set, so
the OCaml runtime is compiled for it too; without it the binary is marked for the build
machine's macOS (`minos 26.0`) and older ones refuse to start it. The linker signs it (ad hoc);
Apple silicon runs nothing unsigned, so re-sign with `codesign -s - -f` after any `strip`.

```bash
export MACOSX_DEPLOYMENT_TARGET=11.0
opam switch create qccd-release 5.3.0 && opam install --switch=qccd-release dune yojson.2.2.2   # once
(cd /tmp/tc/src/Compiler/ocaml && eval "$(opam env --switch=qccd-release --set-switch)" \
   && rm -rf _build && dune build bin/qccdc_cli.exe)
otool -l /tmp/tc/src/Compiler/ocaml/_build/default/bin/qccdc_cli.exe | grep minos   # 11.0
cp /tmp/tc/src/Compiler/ocaml/_build/default/bin/qccdc_cli.exe /tmp/tc/pub/$V/qccdc_cli-macos-arm64
gzip -9 -k /tmp/tc/pub/$V/qccdc_cli-macos-arm64

# the checker, from the same commit's Compiler/lean (the checker package needs no Mathlib)
git archive $C Compiler/lean | tar -x -C /tmp/tc/src
(cd /tmp/tc/src/Compiler/lean/checker && rm -rf .lake && lake build)       # with the deployment target
QV=$(python3 -c "import json,subprocess as s; from qccd.workspace.toolchain import QCHECK_SOURCES as P, qcheck_version as v; \
print(v({p: s.check_output(['git','rev-parse','--verify','-q','$C:'+p], text=True).strip() for p in P}))")
mkdir -p /tmp/tc/pub/qcheck/$QV && Q=/tmp/tc/pub/qcheck/$QV/qcheck-macos-arm64
cp /tmp/tc/src/Compiler/lean/checker/.lake/build/bin/qcheck $Q && strip -x $Q && codesign -s - -f $Q
gzip -9 -k $Q
(cd /tmp/tc/pub/$V && shasum -a 256 qccdc_cli-* > SHA256SUMS; cd /tmp/tc/pub/qcheck/$QV && shasum -a 256 qcheck-* > SHA256SUMS)
```

The qcheck files go under `toolchain/qcheck/$QV/` on the server, beside `toolchain/qccdc/$V/`.

Check each before publishing:
- `QCCD_QCCDC=<the .exe> pytest tests/test_workspace_runs.py tests/test_workspace_grading.py`
  (macOS: also `QCCD_QCHECK=<qcheck>`; the grading tests then run the Lean stage with it)
- the Linux one parses a circuit in stock `debian:12-slim` and `ubuntu:22.04` containers.

Then upload the directory, and check what is served against the local hashes:

```bash
tar -czf - -C /tmp/tc/pub $V | ssh root@qccd.academy 'tar -xzf - -C /var/www/qccd-downloads/toolchain/qccdc && chown -R root:root /var/www/qccd-downloads'
curl -fsS https://qccd.academy/downloads/toolchain/qccdc/$V/SHA256SUMS
# the checker, when there is a new one
tar -czf - -C /tmp/tc/pub/qcheck $QV | ssh root@qccd.academy 'mkdir -p /var/www/qccd-downloads/toolchain/qcheck && tar -xzf - -C /var/www/qccd-downloads/toolchain/qcheck && chown -R root:root /var/www/qccd-downloads'
curl -fsS https://qccd.academy/downloads/toolchain/qcheck/$QV/SHA256SUMS
```

Last, write `toolchain.json`: for each tool the version, the source commit and tree (`qcheck`:
its `paths`), and each file's URL, size in bytes and SHA-256 (gzipped files: also the unpacked
size and SHA-256). Commit it with the source it was built from.

## Serving

The files live in `/var/www/qccd-downloads/` on the droplet, outside the site root, because the
site deploy runs `rsync --delete` into `/var/www/qccd.academy`. nginx serves them through
`nginx-qccd-downloads.conf`, installed as `/etc/nginx/snippets/qccd-downloads.conf` and
included in qccd.academy's HTTPS server block after `qccd-official.conf`.

First release: version `73864a5c493d`, from commit `3df0d19`, published 2026-09-23.
