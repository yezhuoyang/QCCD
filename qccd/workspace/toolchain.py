"""The prebuilt compiler: `qccd toolchain install`.

A fresh clone carries the compiler's OCaml source (`Compiler/ocaml`) but no binary, and
building one needs an OCaml toolchain.  So the project publishes the compiler, `qccdc_cli`,
built from a known source tree, for Windows and Linux on x86-64.  `toolchain.json` pins, for
each platform, the file's URL, size and SHA-256, and the git tree of `Compiler/ocaml` it was
built from.

`install()` downloads the file for this computer and refuses it unless its size and SHA-256
match; nothing is executed before they do.  It then puts it under `~/.qccd/toolchain/`
(`QCCD_HOME` moves that), and runs it once on a two-qubit circuit before calling it
installed, so a binary this machine cannot run is reported now, not in the middle of a run.

Which compiler the workspace uses (`evaluator.Toolchain.discover`): `QCCD_QCCDC` if set, else
one built in this checkout (`Compiler/ocaml/_build`), else the installed one for the
manifest's version.  A binary installed for another version is not used: the Python side
passes flags that an older compiler silently ignores.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import secrets
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

__all__ = ["manifest", "platform_key", "installed", "installed_path", "install", "status", "ToolchainError"]

MANIFEST = Path(__file__).with_name("toolchain.json")
SOURCE_HINT = ("build it from source instead: install OCaml 5.3 with opam, then `opam install dune "
               "yojson.2.2.2` and, in Compiler/ocaml, `dune build bin/qccdc_cli.exe`")


class ToolchainError(Exception):
    pass


def manifest(path: Path | None = None) -> dict:
    return json.loads(Path(path or MANIFEST).read_text(encoding="utf-8"))


def platform_key() -> str:
    osname = {"win32": "windows", "linux": "linux", "darwin": "macos"}.get(sys.platform, sys.platform)
    m = platform.machine().lower()
    arch = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}.get(m, m)
    return f"{osname}-{arch}"


def home() -> Path:
    return Path(os.environ.get("QCCD_HOME") or (Path.home() / ".qccd"))


def installed_path(man: dict | None = None) -> Path:
    q = (man or manifest())["qccdc_cli"]
    return home() / "toolchain" / "qccdc" / q["version"] / ("qccdc_cli.exe" if os.name == "nt" else "qccdc_cli")


def installed(man: dict | None = None) -> Path | None:
    """The installed compiler for the manifest's version, when its size is the pinned one
    (the full hash is checked by `status` and at install)."""
    try:
        man = man or manifest()
        p = installed_path(man)
        asset = man["qccdc_cli"]["assets"].get(platform_key())
        if asset and p.is_file() and p.stat().st_size == asset["bytes"]:
            return p
    except (OSError, ValueError, KeyError):
        pass
    return None


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _smoke(exe: Path) -> None:
    """Parse a Bell circuit; raise if this machine cannot run the binary or it answers wrong."""
    with tempfile.TemporaryDirectory() as td:
        q, o = Path(td) / "bell.qasm", Path(td) / "bell.json"
        q.write_text('OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\nh q[0];\ncx q[0],q[1];\n', encoding="utf-8")
        try:
            cp = subprocess.run([str(exe), "parse", str(q), "-o", str(o)], capture_output=True, text=True, timeout=60)
        except OSError as exc:
            raise ToolchainError(f"this computer cannot run the downloaded compiler ({exc})") from None
        if cp.returncode != 0 or not o.is_file():
            raise ToolchainError("the downloaded compiler did not run here: " + (cp.stderr or cp.stdout)[-600:])
        if "cx" not in o.read_text(encoding="utf-8", errors="replace"):
            raise ToolchainError("the downloaded compiler ran but did not parse a Bell circuit correctly")


def install(*, force: bool = False, man: dict | None = None, say=print) -> dict:
    man = man or manifest()
    q = man["qccdc_cli"]
    key = platform_key()
    asset = q["assets"].get(key)
    if asset is None:
        raise ToolchainError(f"there is no prebuilt compiler for {key} (there is for "
                             f"{', '.join(sorted(q['assets']))}); {SOURCE_HINT}")
    dest = installed_path(man)
    if dest.is_file() and not force and _sha256(dest) == asset["sha256"]:
        return {"status": "already_installed", "path": str(dest), "version": q["version"]}
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + f".part-{secrets.token_hex(4)}")
    say(f"downloading the compiler for {key} ({asset['bytes'] / 1e6:.1f} MB) from {asset['url']}")
    h, n = hashlib.sha256(), 0
    try:
        req = urllib.request.Request(asset["url"], headers={"User-Agent": "qccd-toolchain/1"})
        with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                n += len(chunk)
                if n > asset["bytes"]:
                    raise ToolchainError(f"the download is larger than the pinned {asset['bytes']} bytes: refused")
                h.update(chunk)
                f.write(chunk)
        if n != asset["bytes"] or h.hexdigest() != asset["sha256"]:
            raise ToolchainError(f"the download does not match toolchain.json (got {n} bytes, sha256 "
                                 f"{h.hexdigest()[:16]}...; expected {asset['bytes']} bytes, "
                                 f"{asset['sha256'][:16]}...): refused, nothing was installed")
        if os.name != "nt":
            os.chmod(part, 0o755)
        os.replace(part, dest)
    except OSError as exc:
        raise ToolchainError(f"could not download the compiler ({exc}); {SOURCE_HINT}") from None
    finally:
        if part.exists():
            part.unlink()
    try:
        _smoke(dest)
    except ToolchainError:
        dest.unlink(missing_ok=True)
        raise
    return {"status": "installed", "path": str(dest), "version": q["version"], "sha256": asset["sha256"]}


def status(man: dict | None = None) -> dict:
    """What the workspace will use, and whether the installed binary is the pinned one."""
    from .evaluator import Toolchain
    man = man or manifest()
    q = man["qccdc_cli"]
    key = platform_key()
    asset = q["assets"].get(key)
    p = installed_path(man)
    out = {"platform": key, "version": q["version"], "source": q["source"], "prebuilt_available": asset is not None,
           "installed": None, "in_use": None}
    if p.is_file():
        ok = asset is not None and _sha256(p) == asset["sha256"]
        out["installed"] = {"path": str(p), "matches_manifest": ok}
    used = Toolchain.discover().qccdc
    if used is not None:
        how = ("QCCD_QCCDC" if os.environ.get("QCCD_QCCDC") else
               "installed" if installed(man) and Path(used) == installed(man) else "built in this checkout")
        out["in_use"] = {"path": str(used), "from": how}
    return out
