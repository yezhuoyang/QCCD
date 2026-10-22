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
           "ToolchainError", "RETIRED_QCCDC", "retired", "upgrade_hint", "build_checker", "run_checker"]

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
    "976ab330cbfe6b82e67d1d732690fec58fc519a3bbe3115dcf317699b993d719": "f88a49f79af1",   # windows-x86_64
    "014c83bc139136dbbcd3d9905b87da7400f2c25eacc4d6b93dd4244b692fd174": "f88a49f79af1",   # linux-x86_64
    # f88a49f79af1 for macos-arm64 is NOT listed yet: no newer Mac build exists, and a Mac with
    # no compiler at all is worse than one whose junction-resting programs R23 then refuses.
    "65a30e83188e6f5d9663bc494c465ca2a36b529e741dfc658c4f2ad7ab03320d": "35bf68427d9c",   # linux-x86_64
    "95951a42795a6ada42a5ccecb84d60b9c17361e16536644a83188896727fc0fc": "35bf68427d9c",   # windows-x86_64
    "a26ffbb5ee873e1b00787fbeaa87880f4613a31b047c875fe065a7e8883dfd21": "02bceb9117e7",   # linux-x86_64
    "b771fdf039679f10ab2aeed8bcf9fc3488d5597b14cfe36b462a2558f2affc9d": "02bceb9117e7",   # windows-x86_64
    "01c23f4f1369764fef261f7b442a54f58d4f18131b7793085dde24f23e082b7d": "c7c535380bfb",   # linux-x86_64
    "68b1ba824008979c16d98d20bcfe893b165cc33129bac8c4b0f086bbf22842b2": "c7c535380bfb",   # windows-x86_64
    # (none of the three was ever built for macos-arm64)
}
_RETIRED_WHY = {
    "73864a5c493d": "it predates the router fix that keeps one waveform per cycle (R22): on a design "
                    "that is not on a leaderboard it puts ions moving in different directions into one "
                    "cycle, so the program it makes breaks R22",
    "f88a49f79af1": "it predates R23: on a design with a site where three or more rails meet it leaves "
                    "ions standing on that junction with empty traps beside it, so the program it makes "
                    "breaks R23",
}
# the dock-and-rotate pass of these three turned a two-qubit `barrier a,b` into a CX and every
# docked contact into a CX whatever its gate (a `cz` too), so a circuit with either compiled to a
# program that does not implement it -- R10 rejects it, though the compiler's own check passed it
# (fixed in 7265883d596f; tests/test_compiler_new_designs.py fails on each of them)
for _v in ("35bf68427d9c", "02bceb9117e7", "c7c535380bfb"):
    _RETIRED_WHY[_v] = ("its dock-and-rotate pass compiles a two-qubit `barrier a,b` as a CX and "
                        "any docked two-qubit gate (a `cz` too) as a CX, so a circuit with either "
                        "compiles to a program that does not implement it (R10)")

MANIFEST = Path(__file__).with_name("toolchain.json")
#: the checker's own package: Lean core only, no Mathlib (its lakefile says why)
CHECKER_DIR = Path(__file__).resolve().parents[2] / "Compiler" / "lean" / "checker"
ELAN_HINT = ("install Lean with elan -- macOS: `brew install elan-init`; Linux: "
             "`curl https://elan.lean-lang.org/elan-init.sh -sSf | sh`; Windows: the installer at "
             "https://github.com/leanprover/elan -- then open a new terminal")
#: what `qcheck` is built from (Compiler/lean/checker/lakefile.toml): its version is a digest of
#: these paths' git object ids, so a change to the Pulse proofs beside them does not stale it
QCHECK_SOURCES = ("Compiler/lean/Main.lean", "Compiler/lean/QCCDC/Cert", "Compiler/lean/lean-toolchain",
                  "Compiler/lean/checker/lakefile.toml")
#: tool -> (directory under ~/.qccd/toolchain, file name without .exe)
_TOOLS = {"qccdc_cli": ("qccdc", "qccdc_cli"), "qcheck": ("qcheck", "qcheck")}


def _source_hint(tool: str = "qccdc_cli") -> str:
    if tool == "qcheck":
        return ("build it from source instead: " + ELAN_HINT + ", and run `qccd toolchain build-checker` "
                "(about a minute; it does not need Mathlib)")
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
            empty = '"moves": [], "gates": [], "gate_sites": ["S0"], "hops": []'
            ok.write_text('{"circuit": "smoke", "arch": "none", "n_qubits": 0, "map": {}, "init": {}, '
                          '"circuit_ops": [], ' + empty + '}', encoding="utf-8")
            bad.write_text('{"circuit": "smoke", "arch": "none", "n_qubits": 2, "map": {"0": "a", "1": "b"}, '
                           '"init": {"a": "S0", "b": "S0"}, "circuit_ops": [{"i": 0, "name": "cx", "qubits": [0, 1]}], '
                           + empty + '}', encoding="utf-8")
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


def _lake() -> str | None:
    import shutil
    found = shutil.which("lake")
    if found:
        return found
    p = Path.home() / ".elan" / "bin" / ("lake.exe" if os.name == "nt" else "lake")
    return str(p) if p.is_file() else None


def build_checker(*, say=print, lake: str | None = None) -> Path:
    """Build `qcheck` from this checkout's source and run it once -- on a certificate it must
    accept and one it must reject -- before calling it built.  The workspace then uses it: a
    checker built in the checkout comes before an installed one (`evaluator.Toolchain.discover`).
    The first build downloads the Lean version the package pins (`lean-toolchain`)."""
    if not (CHECKER_DIR / "lakefile.toml").is_file():
        raise ToolchainError(f"the checker's source is not here ({CHECKER_DIR}): this needs a full checkout")
    lake = lake or _lake()
    if lake is None:
        raise ToolchainError("Lean is not installed: " + ELAN_HINT + ", and run this again")
    say(f"building the checker in {CHECKER_DIR} (about a minute the first time; it does not need Mathlib) ...")
    try:
        cp = subprocess.run([lake, "build", "qcheck"], cwd=CHECKER_DIR, capture_output=True, text=True,
                            timeout=3600)
    except OSError as exc:
        raise ToolchainError(f"could not run {lake} ({exc})") from None
    if cp.returncode != 0:
        raise ToolchainError("the build failed:\n" + (cp.stdout + cp.stderr)[-2000:])
    exe = CHECKER_DIR / ".lake" / "build" / "bin" / ("qcheck.exe" if os.name == "nt" else "qcheck")
    if not exe.is_file():
        raise ToolchainError(f"the build said it succeeded but left no checker at {exe}")
    _smoke(exe, "qcheck")
    return exe


def run_checker(path, *, qcheck: Path | None = None) -> int:
    """Run the checker the workspace uses on one `.qcheck.json`: its own output, its own exit
    code (0 accepted, 1 rejected, 2 unreadable)."""
    if qcheck is None:
        from .evaluator import Toolchain
        qcheck = Toolchain.discover().qcheck
    if qcheck is None:
        raise ToolchainError("there is no checker here: run `qccd toolchain install`, or "
                             "`qccd toolchain build-checker` to build it from source")
    if not Path(path).is_file():
        raise ToolchainError(f"no such file: {path}")
    return subprocess.run([str(qcheck), str(path)]).returncode


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
