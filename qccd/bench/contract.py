"""The compiler contract, `qccd.compiler@1`: what a submitted compiler is and how it is run.

A compiler is a directory with a manifest, `qccd-compiler.json`:

    {"kind": "qccd.compiler", "version": 1,
     "name": "my-router",                       a name people will see on the board
     "about": "one line on what it does",
     "runtime": "python3" | "native",          python3: run under the grader's own Python, which
                                                has numpy, stim, qiskit and this `qccd` package
                                                importable; native: a static linux-x86_64 executable
                                                in the directory (the grader has no build tools)
     "entry": ["python3", "compile.py"],        argv, relative to the directory, no shell
     "build": null | ["python3", "setup.py"]}   run once, before any pair, with no network

For each (circuit, device) pair of a benchmark suite, the grader runs

    <entry> --circuit circuit.qasm --device device.arch.json --expanded device.expanded.json --out DIR

(`device.expanded.json` is the same device in the flat form qccdc reads, from
`Compiler/bridge/export_arch.py`) with the directory as its working directory, no network, a
per-pair time and memory limit and a minimal environment.  It must write

    DIR/program.tsir.json           the hardware program (TSIR), in the device's NATIVE gates
                                    (R, VZ, MS), with `meta.qubit_map` = {"<qubit>": "<ion>"}
    DIR/certificate.qcert.json      optional: a qccdc-format certificate (reported, never required)

and exit 0.  Exit code 4 means "I cannot compile this pair" -- a refusal, which costs coverage
but not eligibility.  Any other exit, a timeout, or a missing or malformed program is recorded
as such.  Cooling is the compiler's decision: R7 (the heating budget) is checked like every
other rule, and `Compiler/bridge/insert_cooling.py` is there to call.

Why a directory and a manifest, not a container image: the grader already runs in a container
with no network, a read-only root and no credentials; one more process under those limits is
a small addition, where running a person's image would need a second sandbox.  Why argv and no
shell: nothing in a manifest is interpreted.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

__all__ = ["CompilerSpec", "ContractError", "load_compiler", "invoke", "Invocation", "MANIFEST",
           "REFUSED_EXIT", "RUNTIMES", "dir_digest", "starter"]

MANIFEST = "qccd-compiler.json"
REFUSED_EXIT = 4
RUNTIMES = ("python3", "native")
#: what a compiler directory may hold: small enough to be a source tree, never a data dump
MAX_FILES = 2000
MAX_BYTES = 64 * 1024 * 1024


class ContractError(ValueError):
    pass


@dataclass(frozen=True)
class CompilerSpec:
    root: Path
    name: str
    runtime: str
    entry: tuple[str, ...]
    build: tuple[str, ...] | None
    about: str = ""
    manifest: Mapping = field(default_factory=dict)

    def argv(self, argv0: tuple[str, ...]) -> list[str]:
        """An entry or build argv with the interpreter resolved: `python3` is the grader's own
        Python, so a compiler sees exactly the libraries the grader has."""
        out = list(argv0)
        if self.runtime == "python3" and out and out[0] in ("python3", "python"):
            out[0] = sys.executable
        elif self.runtime == "native" and out:
            p = (self.root / out[0]).resolve()
            if not str(p).startswith(str(self.root.resolve())):
                raise ContractError("the entry must be inside the compiler directory")
            out[0] = str(p)
        return out

    def digest(self) -> str:
        return dir_digest(self.root)


def dir_digest(root: Path) -> str:
    """The identity of a compiler: every file's path and bytes (what ran is what is named)."""
    h = hashlib.sha256()
    for p in sorted(Path(root).rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            h.update(p.relative_to(root).as_posix().encode() + b"\0")
            h.update(hashlib.sha256(p.read_bytes()).digest())
    return "sha256:" + h.hexdigest()


def load_compiler(root: Path | str) -> CompilerSpec:
    """Read and check a compiler directory's manifest.  Refuses anything the contract does not
    allow, with the reason, before anything runs."""
    root = Path(root).resolve()
    m = root / MANIFEST
    if not m.is_file():
        raise ContractError(f"{root} has no {MANIFEST}: a compiler is a directory with that manifest "
                            f"(`qccd bench init-compiler DIR` writes a working one)")
    try:
        man = json.loads(m.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ContractError(f"{MANIFEST} is not JSON: {exc}") from None
    if man.get("kind") != "qccd.compiler" or man.get("version") != 1:
        raise ContractError(f'{MANIFEST} must say "kind": "qccd.compiler", "version": 1')
    name = str(man.get("name") or "").strip()
    if not name or len(name) > 80:
        raise ContractError("the compiler needs a name of 1-80 characters")
    runtime = man.get("runtime")
    if runtime not in RUNTIMES:
        raise ContractError(f"runtime must be one of {RUNTIMES}, not {runtime!r}")
    entry = man.get("entry")
    if not (isinstance(entry, list) and entry and all(isinstance(x, str) and x for x in entry)):
        raise ContractError('entry must be a non-empty argv list, e.g. ["python3", "compile.py"]')
    build = man.get("build")
    if build is not None and not (isinstance(build, list) and build and all(isinstance(x, str) for x in build)):
        raise ContractError("build must be null or an argv list")
    files = [p for p in root.rglob("*") if p.is_file()]
    if len(files) > MAX_FILES or sum(p.stat().st_size for p in files) > MAX_BYTES:
        raise ContractError(f"a compiler directory holds at most {MAX_FILES} files and "
                            f"{MAX_BYTES // (1024 * 1024)} MB")
    for p in root.rglob("*"):
        if p.is_symlink():
            raise ContractError(f"{p.relative_to(root)} is a link: a compiler directory holds only files")
    return CompilerSpec(root=root, name=name, runtime=runtime, entry=tuple(entry),
                        build=tuple(build) if build else None, about=str(man.get("about") or "")[:300],
                        manifest=man)


def _env(workdir: Path) -> dict:
    """A minimal environment: no credentials, no proxies, the repository importable."""
    repo = Path(__file__).resolve().parents[2]
    keep = ("PATH", "SYSTEMROOT", "LANG", "LC_ALL", "LC_CTYPE", "TEMP", "TMP", "TMPDIR",
            "QCCD_QCCDC", "QCCD_QCHECK", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS")
    env = {k: v for k, v in os.environ.items() if k in keep}
    env["HOME"] = str(workdir)
    env["PYTHONPATH"] = str(repo)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


@dataclass
class Invocation:
    status: str                 # ok | refused | timeout | crash | cancelled | no_output
    seconds: float
    program: Path | None
    certificate: Path | None
    log: str

    def to_json(self) -> dict:
        return {"status": self.status, "seconds": round(self.seconds, 3), "log": self.log[-2000:],
                "certificate": self.certificate is not None}


def build(spec: CompilerSpec, *, workdir: Path, timeout: float = 600.0, mem_mb: int = 8192,
          cancel: threading.Event | None = None) -> tuple[bool, str]:
    """Run the manifest's build step once.  A compiler without one is ready as it is."""
    from ..workspace.procs import run_limited
    if not spec.build:
        return True, ""
    r = run_limited(spec.argv(spec.build), cwd=spec.root, timeout=timeout, mem_mb=mem_mb, cancel=cancel,
                    env=_env(Path(workdir)))
    return r.ok, (r.stdout + "\n" + r.stderr)[-4000:]


def invoke(spec: CompilerSpec, circuit: Path, device: Path, expanded: Path, out: Path, *,
           timeout: float, mem_mb: int, cancel: threading.Event | None = None) -> Invocation:
    """Run the compiler on one pair and say what came of it; never raises for the compiler's faults."""
    from ..workspace.procs import run_limited
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    argv = spec.argv(spec.entry) + ["--circuit", str(circuit), "--device", str(device),
                                    "--expanded", str(expanded), "--out", str(out)]
    r = run_limited(argv, cwd=spec.root, timeout=timeout, mem_mb=mem_mb, cancel=cancel, env=_env(out))
    log = (r.stdout + "\n" + r.stderr).strip()
    prog, cert = out / "program.tsir.json", out / "certificate.qcert.json"
    cert = cert if cert.is_file() else None
    if r.status in ("timeout", "cancelled"):
        return Invocation(r.status, r.seconds, None, None, log)
    if r.returncode == REFUSED_EXIT:
        return Invocation("refused", r.seconds, None, None, log)
    if not r.ok:
        return Invocation("crash", r.seconds, None, None, log or f"exit {r.returncode}")
    if not prog.is_file():
        return Invocation("no_output", r.seconds, None, cert, log + "\n(exit 0 but no program.tsir.json)")
    return Invocation("ok", r.seconds, prog, cert, log)


# ---------------------------------------------------------------------- a starting point

STARTER_MANIFEST = {
    "kind": "qccd.compiler", "version": 1, "name": "my-compiler",
    "about": "starts as the reference compiler (qccdc + cooling); replace compile.py with your own",
    "runtime": "python3", "entry": ["python3", "compile.py"], "build": None,
}

STARTER_SOURCE = '''"""A QCCD compiler under the qccd.compiler@1 contract (see qccd/bench/contract.py).

It starts as the reference compiler -- qccdc, then cooling -- so it passes the suite as it is.
Replace `compile_pair` with your own: read the circuit and the device, write a TSIR program
in native gates (R, VZ, MS) with meta.qubit_map, exit 4 to refuse a pair.

    qccd bench run --compiler .          grade it on the public suite, locally
"""
import sys

from qccd.bench.baseline import main as reference


def compile_pair(argv):
    return reference(argv)


if __name__ == "__main__":
    sys.exit(compile_pair(sys.argv[1:]))
'''


def starter(dest: Path) -> Path:
    """Write a working compiler directory to start from."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    if (dest / MANIFEST).exists():
        raise ContractError(f"{dest} already has a {MANIFEST}")
    (dest / MANIFEST).write_text(json.dumps(STARTER_MANIFEST, indent=1) + "\n", encoding="utf-8")
    (dest / "compile.py").write_text(STARTER_SOURCE, encoding="utf-8")
    return dest


def describe() -> dict[str, Any]:
    """The contract as a document, for agents (reference section 'compiler')."""
    return {"contract": "qccd.compiler@1", "manifest_file": MANIFEST, "runtimes": list(RUNTIMES),
            "invocation": "<entry> --circuit circuit.qasm --device device.arch.json "
                          "--expanded device.expanded.json --out DIR",
            "outputs": {"DIR/program.tsir.json": "required on exit 0: native gates R, VZ, MS; meta.qubit_map",
                        "DIR/certificate.qcert.json": "optional; reported, never required"},
            "exit_codes": {"0": "compiled", str(REFUSED_EXIT): "refused (costs coverage, not eligibility)",
                           "other": "crash"},
            "starter": STARTER_MANIFEST, "doc": __doc__}


# ---------------------------------------------------------------------- the submission archive

SUBMISSION = "qccd-submission.json"


def pack(spec: CompilerSpec, suite) -> tuple[bytes, dict]:
    """A compiler directory as the zip the official server takes, bound to one suite release:
    every file of the directory, plus `qccd-submission.json` naming the suite and the digests."""
    import io
    import zipfile
    binding = {"kind": "qccd.compiler_submission", "version": 1,
               "suite": {"id": suite.id, "digest": suite.digest},
               "compiler": {"name": spec.name, "digest": spec.digest(), "runtime": spec.runtime}}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(spec.root.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts and p.name != SUBMISSION:
                z.write(p, p.relative_to(spec.root).as_posix())
        z.writestr(SUBMISSION, json.dumps(binding, indent=1, sort_keys=True))
    return buf.getvalue(), binding


def unpack(archive: bytes, dest: Path) -> tuple[CompilerSpec, dict]:
    """Extract a submitted compiler archive SAFELY (it is data until the sandbox runs it): no
    absolute paths, no `..`, no links, no duplicates, bounded members, size and compression
    ratio.  Returns the loaded compiler and its suite binding; refuses with ContractError."""
    import io
    import stat
    import zipfile
    dest = Path(dest)
    if len(archive) > MAX_BYTES:
        raise ContractError("the archive is larger than 64 MB")
    try:
        z = zipfile.ZipFile(io.BytesIO(archive))
    except zipfile.BadZipFile:
        raise ContractError("not a zip archive") from None
    infos = z.infolist()
    if len(infos) > MAX_FILES + 1:
        raise ContractError(f"more than {MAX_FILES} files")
    total, seen = 0, set()
    for i in infos:
        name = i.filename
        if name.endswith("/"):
            continue
        parts = Path(name).parts
        if name.startswith(("/", "\\")) or ".." in parts or ":" in name or "\\" in name or not parts:
            raise ContractError(f"unsafe path {name!r}")
        if stat.S_ISLNK(i.external_attr >> 16):
            raise ContractError(f"{name!r} is a link")
        if name in seen:
            raise ContractError(f"{name!r} appears twice")
        seen.add(name)
        total += i.file_size
        if total > MAX_BYTES or (i.compress_size and i.file_size / max(i.compress_size, 1) > 200):
            raise ContractError("the archive expands too far")
    if SUBMISSION not in seen or MANIFEST not in seen:
        raise ContractError(f"the archive must hold {MANIFEST} and {SUBMISSION} at its root")
    dest.mkdir(parents=True, exist_ok=True)
    for i in infos:
        if i.filename.endswith("/"):
            continue
        out = dest / i.filename
        out.parent.mkdir(parents=True, exist_ok=True)
        with z.open(i) as src:
            out.write_bytes(src.read(MAX_BYTES + 1))
        if (i.external_attr >> 16) & 0o111:
            out.chmod(0o755)                   # a native entry must stay executable
    binding = json.loads((dest / SUBMISSION).read_text(encoding="utf-8"))
    (dest / SUBMISSION).unlink()
    if binding.get("kind") != "qccd.compiler_submission" or not (binding.get("suite") or {}).get("id"):
        raise ContractError(f"{SUBMISSION} does not name a suite")
    return load_compiler(dest), binding
