"""One QCCDCert compilation, from the three inputs to every program it passes through.

    from qccd.cert.compile import compile_dir
    c = compile_dir(work, hardware, rules, qasm_text)
    c.finish, c.parallel["finish"], c.lines["timed"]

Steps (the commands the paper's artifact runs; nothing here decides anything):

1. write the hardware program and the rule program in the compiler's strict file form;
2. ``qccd-rules``: elaborate the rules on this device (quantifiers expanded over its sites,
   paths and channels) into the device file the compiler reads;
3. ``qccd-dynamic compile``: every retained stage, source to timed target code;
4. ``qccd-dynamic target``: the target code with the parallel schedule (and, with chain
   ports, the chain-ordered timeline);
5. ``qccd-dynamic text``: the compiler's own text of the target code.

If the compiler refuses (a missing calibration, an unroutable placement, a rule that no
schedule satisfies) `ToolFailed` carries its message and no target code exists.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import frames as F
from .syntax import Printer, rules_program
from .toolchain import Toolchain, find

STAGES = ("effects", "pulses", "bound", "placed", "routed", "packed", "timed")


@dataclass
class Compilation:
    folder: Path
    hardware: dict            # the device as written (before elaboration)
    rules: list[dict]         # the rule program as written
    qasm: str
    stages: dict
    target: dict
    parallel: dict
    chain: dict | None
    text: str                 # the compiler's own print of the target code
    fingerprint: dict
    lines: dict = field(default_factory=dict)

    @property
    def printer(self) -> Printer:
        return Printer(self.hardware)

    @property
    def execution(self) -> dict:
        return self.target["execution"]

    @property
    def finish(self) -> int:
        return self.target["finalTick"]

    def frames(self, timeline: bool = False) -> list[F.Frame]:
        starts = self.parallel["starts"] if timeline and self.parallel.get("starts") else None
        return F.frames(self.hardware, self.execution, starts, self.printer)


def _write(path: Path, value) -> None:
    path.write_text(value if isinstance(value, str) else json.dumps(value, indent=1) + "\n",
                    encoding="utf-8", newline="\n")


def _relative(path: Path, folder: Path) -> None:
    """The compiler records where it read the source (`sourceRoot`, the bundle's unit ids):
    rewrite this machine's absolute folder to `.`, so no local path is published."""
    text = path.read_text(encoding="utf-8")
    absolute = str(folder.resolve())
    for form in {absolute, absolute.replace("\\", "/"), absolute[0].lower() + absolute[1:],
                 (absolute[0].lower() + absolute[1:]).replace("\\", "/")}:
        text = text.replace(json.dumps(form)[1:-1], ".")
    path.write_text(text, encoding="utf-8", newline="\n")


def compile_dir(folder: str | Path, hardware: dict, rules: list[dict], qasm: str, *,
                flags: tuple[str, ...] = (), chain_ports: dict | None = None,
                suffix: str = "", tc: Toolchain | None = None, profile: str | None = None) -> Compilation:
    """Compile `qasm` for `hardware` under `rules`, writing every file into `folder`.
    `profile="qiskit"` lets the source include qelib1.inc (the compiler's compatibility
    definitions); the default is the strict profile."""
    tc = tc or find()
    prof = ("--qasm-profile", profile) if profile else ()
    flags = (*flags, *prof)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    tc.strict("hardware", {**hardware, "rules": []}, folder / "hardware.json")
    tc.strict("rules", rules, folder / f"rules{suffix}.json")
    _write(folder / f"rules{suffix}.txt", "\n".join(rules_program(rules, hardware)) + "\n")
    _write(folder / "main.qasm", qasm)
    eff = f"effective{suffix}.json"
    tc.run("qccd-rules", "hardware.json", f"rules{suffix}.json", eff, cwd=folder)
    extra = ["--parallel-output", f"parallel{suffix}.json", *flags]
    if chain_ports is not None:
        declared = {"chains": chain_ports.get("chains"), "ports": chain_ports["ports"]}
        _write(folder / "chain-ports.json", json.dumps(declared, separators=(",", ":"), sort_keys=True) + "\n")
        extra += ["--chain-ports", "chain-ports.json", "--chain-output", f"chain{suffix}.json"]
    tc.run("qccd-dynamic", "compile", eff, "main.qasm", f"stages{suffix}.json", *prof, cwd=folder)
    tc.run("qccd-dynamic", "target", eff, "main.qasm", f"target{suffix}.json", *extra, cwd=folder)
    text = tc.run("qccd-dynamic", "text", eff, "main.qasm", *flags, cwd=folder)
    _write(folder / f"execution{suffix}.qccd", text)
    for name in (f"stages{suffix}.json", f"target{suffix}.json"):
        _relative(folder / name, folder)
    fp = tc.fingerprint()
    _write(folder / "compiler.json", fp)
    return load(folder, hardware, rules, qasm, suffix=suffix, fingerprint=fp)


def load(folder: str | Path, hardware: dict | None = None, rules: list[dict] | None = None,
         qasm: str | None = None, *, suffix: str = "", fingerprint: dict | None = None) -> Compilation:
    """Read a compilation written by `compile_dir` (no toolchain needed)."""
    folder = Path(folder)
    rd = lambda n: json.loads((folder / n).read_text(encoding="utf-8"))  # noqa: E731
    hardware = hardware if hardware is not None else rd("hardware.json")
    rules = rules if rules is not None else rd(f"rules{suffix}.json")
    qasm = qasm if qasm is not None else (folder / "main.qasm").read_text(encoding="utf-8")
    chain = rd(f"chain{suffix}.json") if (folder / f"chain{suffix}.json").exists() else None
    c = Compilation(folder, hardware, rules, qasm, rd(f"stages{suffix}.json"), rd(f"target{suffix}.json"),
                    rd(f"parallel{suffix}.json"), chain,
                    (folder / f"execution{suffix}.qccd").read_text(encoding="utf-8"),
                    fingerprint or (rd("compiler.json") if (folder / "compiler.json").exists() else {}))
    c.lines = listings(c)
    return c


def listings(c: Compilation) -> dict[str, list[str]]:
    """Every program of the compilation in the paper's syntax, keyed by stage."""
    p = c.printer
    s = c.stages
    out = {"hardware": p.hardware_program(), "rules": rules_program(c.rules, c.hardware),
           "source": c.qasm.splitlines()}
    out["effects"] = [p.token(t) for t in s["frontend"]["expanded"]["code"]]
    out["pulses"] = [p.token(t) for t in s["pulses"]["code"]]
    out["bound"] = [p.token(t) for t in s["bound"]["code"]]
    out["placed"] = p.program(s["placed"])[0]
    out["routed"] = p.program(s["routed"])[0]
    out["packed"] = p.program(s["packed"])[0]
    out["timed"], finish = p.program(s["timed"], "sequential")
    if finish != c.target["finalTick"]:
        raise AssertionError(f"printed finish {finish} differs from the compiler's finalTick {c.target['finalTick']}")
    if c.parallel.get("starts"):
        out["parallel"] = p.program(s["timed"], c.parallel["starts"])[0]
    if c.chain:
        out["chain"] = p.chain_lines(s["timed"], c.chain)
    return out
