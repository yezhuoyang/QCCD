"""The pinned FormalRV toolchain: run its stages and its VM, never edit it.

FormalRV is a separate project with its own owners (docs/FULLSTACK_PLAN.md §0, §6).  QCCD uses a
private clone at one commit (`PIN_COMMIT`), built there:

    compiler/_build/default/bin/{prepare_qppm,prepare_qec,qec_sys,qec_sys_staged}.exe
        dune build, OCaml 5.3 (opam switch `default`: the compiler uses Domain)
    .lake/build/bin/nexpr_canon.exe
        FormalRV's verified expression normaliser, which qec_sys starts as a child process; it
        imports only FormalRV.Core.Symbolic.{NExpr,Poly}, so it was built from the pinned
        sources in a standalone Lake project (no Mathlib) and copied in
    ftq_vm/ocaml/_build/default/resource_vm.exe
        the VM of record

`QCCD_FORMALRV` overrides the clone's location.  Every call checks the clone is still at the
pinned commit; a moved pin is refused, not silently used.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

PIN_COMMIT = "df3a1427bafe5338553d61dfd200133738267203"
#: the clone sits beside this repository unless QCCD_FORMALRV says otherwise
DEFAULT_PIN = Path(__file__).resolve().parents[3] / "FormalRV-pin"
OPAM_SWITCH = Path(os.environ.get("QCCD_OPAM_SWITCH")
                   or Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "opam" / "default")


class ToolchainError(RuntimeError):
    pass


def root() -> Path:
    return Path(os.environ.get("QCCD_FORMALRV") or DEFAULT_PIN)


def commit() -> str:
    r = subprocess.run(["git", "-C", str(root()), "rev-parse", "HEAD"], capture_output=True, text=True)
    if r.returncode:
        raise ToolchainError(f"no FormalRV clone at {root()}: {r.stderr.strip()}")
    return r.stdout.strip()


def check_pin() -> None:
    c = commit()
    if c != PIN_COMMIT:
        raise ToolchainError(f"FormalRV at {root()} is at {c[:10]}, the pin is {PIN_COMMIT[:10]}")


def _env() -> dict:
    env = dict(os.environ)
    env["PATH"] = str(OPAM_SWITCH / "bin") + os.pathsep + env.get("PATH", "")
    return env


def _exe(rel: str) -> Path:
    p = root() / rel
    if not p.exists():
        raise ToolchainError(f"{p} is not built (see this module's docstring)")
    return p


def _run(args: list, what: str, cwd: Path | None = None, timeout: float = 3600) -> str:
    r = subprocess.run([str(a) for a in args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(cwd or root() / "compiler"), env=_env(), timeout=timeout)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode:
        raise ToolchainError(f"{what} refused (exit {r.returncode}): {out.strip()[-1500:]}")
    return out


def prog_to_qppm(source: Path, out: Path) -> str:
    check_pin()
    return _run([_exe("compiler/_build/default/bin/prepare_qppm.exe"), "--source", Path(source).resolve(),
                 "-o", Path(out).resolve()], "prepare_qppm")


def qppm_to_qec(qppm: Path, out: Path, code: str, distance: int, specialize: dict | None = None) -> str:
    check_pin()
    args = [_exe("compiler/_build/default/bin/prepare_qec.exe"), Path(qppm).resolve(), "--code", code,
            "--distance", distance]
    for k, v in (specialize or {}).items():
        args += ["--specialize", f"{k}={v}"]
    return _run(args + ["-o", Path(out).resolve()], "prepare_qec")


def qec_to_sys(qec: Path, machine: Path, sysspec: Path, out: Path, staged: bool = True) -> str:
    """`.qec` -> `.sysprog`.  Run inside the clone: the compiler finds nexpr_canon by walking up
    from its working directory to FormalRV's lakefile."""
    check_pin()
    exe = "qec_sys_staged.exe" if staged else "qec_sys.exe"
    return _run([_exe(f"compiler/_build/default/bin/{exe}"), Path(qec).resolve(), "--machine",
                 Path(machine).resolve(), "--system-spec", Path(sysspec).resolve(), "-o",
                 Path(out).resolve()], exe)


def vm(sysprog: Path) -> dict:
    """FormalRV's OCaml VM verdict (`--json`).  A refusal is returned, not raised."""
    check_pin()
    exe = _exe("ftq_vm/ocaml/_build/default/resource_vm.exe")
    r = subprocess.run([str(exe), str(Path(sysprog).resolve()), "--json"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=_env(), timeout=3600, cwd=str(root()))
    try:
        return json.loads(r.stdout)
    except ValueError:
        raise ToolchainError(f"the VM printed no verdict (exit {r.returncode}): {(r.stderr or r.stdout)[-800:]}")


def provenance() -> dict:
    """What a result records about the FormalRV it came from."""
    return {"formalrv_commit": commit(), "pin": PIN_COMMIT}
