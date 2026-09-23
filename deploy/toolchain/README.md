# The prebuilt compiler (`qccd toolchain install`)

A fresh clone has the compiler's OCaml source but no binary. `qccd toolchain install`
(`qccd/workspace/toolchain.py`) downloads a prebuilt `qccdc_cli` for Windows or Linux on x86-64
from `https://qccd.academy/downloads/toolchain/qccdc/<version>/`. It refuses the file unless its
size and SHA-256 match `qccd/workspace/toolchain.json`, installs it under `~/.qccd/toolchain/`,
and runs it once on a Bell circuit. macOS has no prebuilt binary: build it from source.

`<version>` is the first 12 hex digits of the git tree of `Compiler/ocaml` the binaries were
built from. `tests/test_workspace_toolchain.py` fails when a commit changes `Compiler/ocaml`
without a new release, because installed binaries would then be older than the source the
Python side expects.

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

Check both before publishing:
- `QCCD_QCCDC=<the .exe> pytest tests/test_workspace_runs.py tests/test_workspace_grading.py`
- the Linux one parses a circuit in stock `debian:12-slim` and `ubuntu:22.04` containers.

Then upload the directory, and check what is served against the local hashes:

```bash
tar -czf - -C /tmp/tc/pub $V | ssh root@165.232.55.161 'tar -xzf - -C /var/www/qccd-downloads/toolchain/qccdc && chown -R root:root /var/www/qccd-downloads'
curl -fsS https://qccd.academy/downloads/toolchain/qccdc/$V/SHA256SUMS
```

Last, write `toolchain.json`: the version, the source commit and tree, and each file's URL,
size in bytes and SHA-256. Commit it with the source it was built from.

## Serving

The files live in `/var/www/qccd-downloads/` on the droplet, outside the site root, because the
site deploy runs `rsync --delete` into `/var/www/qccd.academy`. nginx serves them through
`nginx-qccd-downloads.conf`, installed as `/etc/nginx/snippets/qccd-downloads.conf` and
included in qccd.academy's HTTPS server block after `qccd-official.conf`.

First release: version `73864a5c493d`, from commit `3df0d19`, published 2026-09-23.
