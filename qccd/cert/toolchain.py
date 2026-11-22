"""Find and run the QCCDCert build.

QCCDCert lives in its own Lean repository (Lean 4 + Mathlib).  This repository does not
vendor it: set ``QCCD_CERT`` to a checkout whose ``lake build`` has run, or keep the checkout
beside this one as ``../ISCA27ArtifactQCCD``.  Everything this package writes records the
fingerprint of the build it used (`Toolchain.fingerprint`): a digest of the compiler's Lean
sources, so a page can say exactly which compiler produced it.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT = ROOT.parent / "ISCA27ArtifactQCCD"


class ToolchainMissing(RuntimeError):
    """No QCCDCert build was found."""


class ToolFailed(RuntimeError):
    """A QCCDCert tool exited with an error (the compiler refused, or an input was malformed)."""

    def __init__(self, command: list[str], output: str):
        super().__init__(f"{' '.join(command)}\n{output[-3000:]}")
        self.command = command
        self.output = output


@dataclass(frozen=True)
class Toolchain:
    root: Path

    @property
    def bin(self) -> Path:
        return self.root / ".lake" / "build" / "bin"

    def exe(self, name: str) -> Path:
        for cand in (self.bin / f"{name}.exe", self.bin / name):
            if cand.exists():
                return cand
        raise ToolchainMissing(f"{name} is not built in {self.bin}: run `lake build` in {self.root}")

    def run(self, tool: str, *args, cwd: Path | None = None, timeout: float = 3600) -> str:
        cmd = [str(self.exe(tool)), *map(str, args)]
        done = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
        if done.returncode:
            raise ToolFailed(cmd, done.stdout + done.stderr)
        return done.stdout

    def fingerprint(self) -> dict:
        """sha256 over the compiler's Lean sources (sorted paths + bytes, CRLF-normalised)."""
        h = hashlib.sha256()
        files = sorted([*self.root.glob("QCCD/**/*.lean"), *self.root.glob("*Main.lean"),
                        self.root / "lakefile.toml", self.root / "lean-toolchain"])
        for f in files:
            if f.exists():
                h.update(f.relative_to(self.root).as_posix().encode() + b"\0")
                h.update(f.read_bytes().replace(b"\r\n", b"\n") + b"\0")
        toolchain = (self.root / "lean-toolchain").read_text(encoding="utf-8").strip() \
            if (self.root / "lean-toolchain").exists() else "?"
        return {"compiler": "QCCDCert", "sources_sha256": h.hexdigest()[:16],
                "files": len(files), "lean": toolchain}

    # ------------------------------------------------------------------ strict files
    def strict(self, kind: str, value, path: Path) -> Path:
        """Write an input file in the strict form the compiler reads (`qccd-rules strict`
        prints it with the printer whose reader is proved: no member can be ignored)."""
        path = Path(path)
        draft = path.with_name(path.name + ".draft")
        draft.write_text(json.dumps(value) + "\n", encoding="utf-8", newline="\n")
        try:
            self.run("qccd-rules", "strict", kind, draft, path)
        finally:
            draft.unlink(missing_ok=True)
        return path


def find(root: str | Path | None = None) -> Toolchain:
    """The QCCDCert build: `root`, else ``$QCCD_CERT``, else ``../ISCA27ArtifactQCCD``.
    ``QCCD_CERT=off`` turns QCCDCert off (the studio's `auto` then uses the project's own
    compiler, as on a machine without QCCDCert)."""
    if not root and os.environ.get("QCCD_CERT", "").strip().lower() in ("off", "0", "none", "no"):
        raise ToolchainMissing("QCCDCert is turned off (QCCD_CERT=off)")
    cands = [Path(root)] if root else []
    if os.environ.get("QCCD_CERT"):
        cands.append(Path(os.environ["QCCD_CERT"]))
    cands.append(DEFAULT)
    for c in cands:
        if (c / "lakefile.toml").exists() and (c / ".lake" / "build" / "bin").exists():
            return Toolchain(c.resolve())
    raise ToolchainMissing(
        "QCCDCert is not available: set QCCD_CERT to a checkout of the QCCDCert compiler "
        "on which `lake build` has run (looked in: " + ", ".join(map(str, cands)) + ")")


def available() -> bool:
    try:
        find()
        return True
    except ToolchainMissing:
        return False
