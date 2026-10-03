"""The prebuilt compiler and checker: `qccd toolchain install`.

A fresh clone carries the compiler's OCaml source (`Compiler/ocaml`) and the Lean checker's
source (`Compiler/lean`) but no binaries, and building them needs an OCaml and a Lean
toolchain.  So the project publishes the compiler, `qccdc_cli`, for Windows and Linux on
x86-64 and macOS on Apple silicon, and the proved checker, `qcheck`, where one has been
built.  `toolchain.json` pins, for each tool and platform, the file's URL, size and SHA-256,
and the git source it was built from.

`install()` downloads the files for this computer and refuses each unless its size and
SHA-256 match; nothing is executed before they do.  It then puts them under
`~/.qccd/toolchain/` (`QCCD_HOME` moves that), and runs each once -- the compiler on a
two-qubit circuit, the checker on a certificate it must accept and one it must reject --
before calling it installed, so a binary this machine cannot run is reported now, not in the
middle of a run.

Which binaries the workspace uses (`evaluator.Toolchain.discover`): `QCCD_QCCDC` /
`QCCD_QCHECK` if set, else one built in this checkout, else the installed one for the
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

__all__ = ["manifest", "platform_key", "qcheck_version", "QCHECK_SOURCES", "installed", "installed_qcheck", "installed_path", "install", "status",
           "ToolchainError", "RETIRED_QCCDC", "retired", "upgrade_hint"]

#: compiler releases withdrawn because they emit programs that break a rule: the SHA-256 of
#: each published binary (unpacked) -> its version.  A machine that installed one keeps it
#: under ~/.qccd/toolchain until something replaces it, and a checkout that predates the pin
#: change still points at it -- which is how a Mac compiled a BB round on a new grid in
#: October and animated cycles in which ions moved in different directions.  The digest is
#: what is checked, so a copy renamed or moved is caught as well.
RETIRED_QCCDC = {
    "8b2a8d1d6471ae2bbc9a9248270260c9980d9e4aee7f6097bb7b776492155234": "73864a5c493d",   # windows-x86_64
    "ae79e047165f5260130eac7b2742a90634a6494dab19f0da17d146948eb81c64": "73864a5c493d",   # linux-x86_64
    "8082f6b2eb873e10d8d956f52dc7803191134a639b0bee9aaa2ac0fe9bda1e35": "73864a5c493d",   # macos-arm64
}
_RETIRED_WHY = {
    "73864a5c493d": "it predates the router fix that keeps one waveform per cycle (R22): on a design "
                    "that is not on a leaderboard it puts ions moving in different directions into one "
                    "cycle, so the program it makes breaks R22",
}

MANIFEST = Path(__file__).with_name("toolchain.json")
#: what `qcheck` is built from (Compiler/lean/checker/lakefile.toml): its version is a digest of
#: these paths' git object ids, so a change to the Pulse proofs beside them does not stale it
QCHECK_SOURCES = ("Compiler/lean/Main.lean", "Compiler/lean/QCCDC/Cert", "Compiler/lean/lean-toolchain",
                  "Compiler/lean/checker/lakefile.toml")
#: tool -> (directory under ~/.qccd/toolchain, file name without .exe)
_TOOLS = {"qccdc_cli": ("qccdc", "qccdc_cli"), "qcheck": ("qcheck", "qcheck")}


def _source_hint(tool: str = "qccdc_cli") -> str:
    if tool == "qcheck":
        return ("build it from source instead: install Lean with elan (macOS: `brew install elan-init`), then "
                "in Compiler/lean/checker run `lake build` (about a minute; it does not need Mathlib)")
    brew = "`brew install opam`, " if sys.platform == "darwin" else ""
    return ("build it from source instead: install OCaml 5.3 with opam (" + brew + "`opam init`, "
            "`opam switch create 5.3.0`), then `opam install dune yojson.2.2.2` and, in Compiler/ocaml, "
            "`dune build bin/qccdc_cli.exe` (the name ends in .exe on every platform)")


SOURCE_HINT = _source_hint()


class ToolchainError(Exception):
    pass


def manifest(path: Path | None = None) -> dict:
    return json.loads(Path(path or MANIFEST).read_text(encoding="utf-8"))


def _rosetta() -> bool:
    """An x86-64 Python on Apple silicon: the machine runs arm64 binaries natively."""
    if sys.platform != "darwin":
        return False
    try:
        import ctypes
        libc = ctypes.CDLL(None)
        v, n = ctypes.c_int(0), ctypes.c_size_t(4)
        return libc.sysctlbyname(b"sysctl.proc_translated", ctypes.byref(v), ctypes.byref(n), None, 0) == 0 \
            and v.value == 1
    except Exception:
        return False


def qcheck_version(object_ids: dict) -> str:
    """The checker's version from `{path: git object id}` over `QCHECK_SOURCES`."""
    text = "\n".join(f"{p} {object_ids[p]}" for p in QCHECK_SOURCES)
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def platform_key() -> str:
    osname = {"win32": "windows", "linux": "linux", "darwin": "macos"}.get(sys.platform, sys.platform)
    m = platform.machine().lower()
    arch = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}.get(m, m)
    if arch == "x86_64" and _rosetta():
        arch = "arm64"
    return f"{osname}-{arch}"


def home() -> Path:
    return Path(os.environ.get("QCCD_HOME") or (Path.home() / ".qccd"))


def installed_path(man: dict | None = None, tool: str = "qccdc_cli") -> Path:
    q = (man or manifest())[tool]
    folder, name = _TOOLS[tool]
    return home() / "toolchain" / folder / q["version"] / (name + (".exe" if os.name == "nt" else ""))


def installed(man: dict | None = None, tool: str = "qccdc_cli") -> Path | None:
    """The installed binary for the manifest's version, when its size is the pinned one
    (the full hash is checked by `status` and at install)."""
    try:
        man = man or manifest()
        if tool not in man:
            return None
        p = installed_path(man, tool)
        asset = man[tool]["assets"].get(platform_key())
        if asset and p.is_file() and p.stat().st_size == _unpacked(asset)[0]:
            return p
    except (OSError, ValueError, KeyError):
        pass
    return None


def installed_qcheck(man: dict | None = None) -> Path | None:
    return installed(man, "qcheck")


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _run(exe: Path, args: list, what: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run([str(exe), *map(str, args)], capture_output=True, text=True, timeout=60)
    except OSError as exc:
        raise ToolchainError(f"this computer cannot run the downloaded {what} ({exc})") from None


def _smoke(exe: Path, tool: str = "qccdc_cli") -> None:
    """Run the binary once; raise if this machine cannot run it or it answers wrong."""
    with tempfile.TemporaryDirectory() as td:
        if tool == "qcheck":
            ok, bad = Path(td) / "ok.json", Path(td) / "bad.json"
            ok.write_text('{"circuit": "smoke", "arch": "none", "n_qubits": 0}', encoding="utf-8")
            bad.write_text('{"circuit": "smoke", "arch": "none", "n_qubits": 2, "map": {"0": "a", "1": "b"}, '
                           '"init": {"a": "S0", "b": "S0"}, "circuit_ops": [{"i": 0, "name": "cx", "qubits": [0, 1]}]}',
                           encoding="utf-8")
            a, r = _run(exe, [ok], "checker"), _run(exe, [bad], "checker")
            if a.returncode != 0 or "ACCEPTED" not in a.stdout:
                raise ToolchainError("the downloaded checker did not run here: " + (a.stderr or a.stdout)[-600:])
            if r.returncode != 1 or "REJECTED" not in r.stdout:
                raise ToolchainError("the downloaded checker ran but accepted a certificate that drops a gate")
            return
        q, o = Path(td) / "bell.qasm", Path(td) / "bell.json"
        q.write_text('OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\nh q[0];\ncx q[0],q[1];\n', encoding="utf-8")
        cp = _run(exe, ["parse", q, "-o", o], "compiler")
        if cp.returncode != 0 or not o.is_file():
            raise ToolchainError("the downloaded compiler did not run here: " + (cp.stderr or cp.stdout)[-600:])
        if "cx" not in o.read_text(encoding="utf-8", errors="replace"):
            raise ToolchainError("the downloaded compiler ran but did not parse a Bell circuit correctly")


_RETIRED_SEEN: dict = {}


def retired(path) -> str | None:
    """Why the compiler at `path` must not be used, if it is a withdrawn release; else None."""
    if path is None:
        return None
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return None
    key = (str(p), st.st_size, st.st_mtime_ns)
    if key not in _RETIRED_SEEN:
        _RETIRED_SEEN[key] = RETIRED_QCCDC.get(_sha256(p))
    version = _RETIRED_SEEN[key]
    if version is None:
        return None
    return f"the compiler at {p} is release {version}, which was withdrawn: {_RETIRED_WHY[version]}"


def upgrade_hint(man: dict | None = None) -> str:
    """What to do about an outdated compiler on THIS computer."""
    man = man or manifest()
    q = man["qccdc_cli"]
    if q["assets"].get(platform_key()):
        return (f"run `qccd toolchain install --force` to get compiler {q['version']} (if Compiler/ocaml is "
                "built in this checkout, rebuild it instead: `dune build bin/qccdc_cli.exe` there), then try again")
    return (f"there is no prebuilt compiler {q['version']} for {platform_key()}, so {_source_hint()}; "
            "then try again")


def _unpacked(asset: dict) -> tuple[int, str]:
    """The installed file's size and SHA-256: the download's, unless it is compressed."""
    if asset.get("encoding") == "gzip":
        return asset["unpacked_bytes"], asset["unpacked_sha256"]
    return asset["bytes"], asset["sha256"]


def _gunzip(part: Path, asset: dict) -> None:
    """Replace the (already verified) gzip download `part` with its content, and refuse that
    too unless it is the pinned file."""
    import gzip
    want_n, want_h = _unpacked(asset)
    tmp = part.with_name(part.name + ".x")
    h, n = hashlib.sha256(), 0
    try:
        with gzip.open(part, "rb") as src, open(tmp, "wb") as dst:
            for chunk in iter(lambda: src.read(1 << 20), b""):
                n += len(chunk)
                if n > want_n:
                    raise ToolchainError(f"the unpacked file is larger than the pinned {want_n} bytes: refused")
                h.update(chunk)
                dst.write(chunk)
        if n != want_n or h.hexdigest() != want_h:
            raise ToolchainError("the unpacked file does not match toolchain.json: refused, nothing was installed")
        os.replace(tmp, part)
    except (OSError, EOFError) as exc:
        raise ToolchainError(f"the download could not be unpacked ({exc}): refused") from None
    finally:
        if tmp.exists():
            tmp.unlink()


def _install_one(man: dict, tool: str, force: bool, say) -> dict:
    q = man[tool]
    key = platform_key()
    asset = q["assets"][key]
    what = "checker" if tool == "qcheck" else "compiler"
    dest = installed_path(man, tool)
    if dest.is_file() and not force and _sha256(dest) == _unpacked(asset)[1]:
        return {"status": "already_installed", "path": str(dest), "version": q["version"]}
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + f".part-{secrets.token_hex(4)}")
    say(f"downloading the {what} for {key} ({asset['bytes'] / 1e6:.1f} MB) from {asset['url']}")
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
        if asset.get("encoding") == "gzip":
            _gunzip(part, asset)
        if os.name != "nt":
            os.chmod(part, 0o755)
        os.replace(part, dest)
    except OSError as exc:
        raise ToolchainError(f"could not download the {what} ({exc}); {_source_hint(tool)}") from None
    finally:
        if part.exists():
            part.unlink()
    try:
        _smoke(dest, tool)
    except ToolchainError:
        dest.unlink(missing_ok=True)
        raise
    return {"status": "installed", "path": str(dest), "version": q["version"], "sha256": _unpacked(asset)[1]}


def install(*, force: bool = False, man: dict | None = None, say=print) -> dict:
    """Install the compiler, and the checker where one is published for this platform.  The
    result is the compiler's; `result["qcheck"]` says what happened to the checker."""
    man = man or manifest()
    q = man["qccdc_cli"]
    key = platform_key()
    if q["assets"].get(key) is None:
        raise ToolchainError(f"there is no prebuilt compiler for {key} (there is for "
                             f"{', '.join(sorted(q['assets']))}); {SOURCE_HINT}")
    out = _install_one(man, "qccdc_cli", force, say)
    if key in (man.get("qcheck") or {}).get("assets", {}):
        out["qcheck"] = _install_one(man, "qcheck", force, say)
    else:
        out["qcheck"] = {"status": "not_available", "hint": _source_hint("qcheck")}
    return out


def status(man: dict | None = None) -> dict:
    """What the workspace will use, and whether the installed binaries are the pinned ones."""
    from .evaluator import Toolchain
    man = man or manifest()
    q = man["qccdc_cli"]
    key = platform_key()
    asset = q["assets"].get(key)
    out = {"platform": key, "version": q["version"], "source": q["source"], "prebuilt_available": asset is not None,
           "installed": None, "in_use": None}
    tc = Toolchain.discover()
    for tool, used, env in (("qccdc_cli", tc.qccdc, "QCCD_QCCDC"), ("qcheck", tc.qcheck, "QCCD_QCHECK")):
        info: dict = {"installed": None, "in_use": None}
        if tool in man:
            a = man[tool]["assets"].get(key)
            info["prebuilt_available"] = a is not None
            p = installed_path(man, tool)
            if p.is_file():
                info["installed"] = {"path": str(p), "matches_manifest": a is not None and _sha256(p) == _unpacked(a)[1]}
        if used is not None:
            how = (env if os.environ.get(env) else
                   "installed" if installed(man, tool) and Path(used) == installed(man, tool) else "built in this checkout")
            info["in_use"] = {"path": str(used), "from": how}
            if tool == "qccdc_cli" and retired(used):
                info["in_use"]["withdrawn"] = retired(used) + "; " + upgrade_hint(man)
        if tool == "qccdc_cli":
            out["installed"], out["in_use"] = info["installed"], info["in_use"]
        else:
            out["qcheck"] = info
    return out
