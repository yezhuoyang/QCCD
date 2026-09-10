"""`python -m qccd.site.compile_examples` -- the basic gates, compiled and verified.

The Compilation page shows every basic QASM gate going through the real pipeline: the
OCaml compiler (`qccdc_cli.exe compile`), the cooling pass, the 22 replayed rules, the
checker input, and R10 by the proved Lean checker and the stabilizer tableau (or the exact
unitary outside the Clifford fragment).  Those tools live on the machine that builds the
repository, not on the machine that builds the site, so this script runs them once and
writes what they produced under `qccd/site/compiled/<id>/`; `python -m qccd site` reads
that directory and never needs a toolchain.  Every artefact on the page is one of these
files, unedited.

    qccd/site/compiled/ring6d.arch.json       the device the gates are compiled onto
    qccd/site/compiled/<id>/circuit.qasm      the input
    qccd/site/compiled/<id>/prog.tsir.json    what the compiler emitted
    qccd/site/compiled/<id>/prog.qcert.json   its certificate
    qccd/site/compiled/<id>/prog.cooled.tsir.json   after the cooling pass
    qccd/site/compiled/<id>/prog.qcheck.json  the checker's input (certificate + device facts)
    qccd/site/compiled/<id>/rules.json        check_tsir --json: the 22 rules
    qccd/site/compiled/<id>/verdict.json      check_cert --json: R10, both halves
    qccd/site/compiled/<id>/meta.json         title, note, the compiler's own summary line
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
COMPILER = ROOT / "Compiler"
QCCDC = COMPILER / "ocaml" / "_build" / "default" / "bin" / "qccdc_cli.exe"
QCHECK = COMPILER / "lean" / ".lake" / "build" / "bin" / "qcheck.exe"
BRIDGE = COMPILER / "bridge"
OUT = HERE / "compiled"

HEADER = 'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\ncreg c[2];\n'

#: (id, the circuit body, title, what to notice).  The first is the worked example the
#: page walks through stage by stage; the rest are the basic gates, one each.
GATES: list[tuple[str, str, str, str]] = [
    ("bell", "h q[0];\ncx q[0],q[1];\nmeasure q -> c;", "A Bell pair",
     "the worked example: a Hadamard, a CNOT that needs transport, and two measurements"),
    ("h", "h q[0];", "h", "a Clifford rotation: one frame update and one beam, u3(π/2, 0, π)"),
    ("x", "x q[0];", "x", "a bit flip: u3(π, 0, π), one beam"),
    ("y", "y q[0];", "y", "u3(π, π/2, π/2), one beam"),
    ("z", "z q[0];", "z", "a phase flip: u3(0, 0, π), so the frame update carries the gate and the emitted beam has angle 0"),
    ("s", "s q[0];", "s", "u3(0, 0, π/2): the frame update carries the gate, the beam has angle 0"),
    ("t", "t q[0];", "t", "non-Clifford: u3(0, 0, π/4); R10's semantics half uses the exact unitary here"),
    ("rx", "rx(0.5) q[0];", "rx(θ)", "an arbitrary angle about x: u3(θ, −π/2, π/2)"),
    ("ry", "ry(0.5) q[0];", "ry(θ)", "u3(θ, 0, 0): one beam"),
    ("rz", "rz(0.7854) q[0];", "rz(θ)", "u3(0, 0, θ): the frame update carries the angle, the beam has angle 0"),
    ("cx", "cx q[0],q[1];", "cx", "the entangler: transport to co-locate, then R, MS, R, R, R (the proved decomposition)"),
    ("cz", "cz q[0],q[1];", "cz", "h · cx · h on the target, so two extra single-qubit pulses around the same MS"),
    ("swap", "swap q[0],q[1];", "swap", "three CNOTs, so three MS pulses at one site"),
    ("measure", "measure q[0] -> c[0];", "measure", "readout in a zone with SPAM; no pulses, so no gate witness"),
    ("reset", "reset q[0];", "reset", "back to |0>: same zone requirement, no witness"),
]


def sh(cmd: list, timeout: int = 900) -> tuple[int, str, float]:
    t0 = time.time()
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout,
                       cwd=str(COMPILER))
    return r.returncode, (r.stdout or "") + (r.stderr or ""), time.time() - t0


def device() -> tuple[Path, Path]:
    """The six-site ring with two dock spurs, the device the rest of the site stands on."""
    from ..api import Machine
    OUT.mkdir(parents=True, exist_ok=True)
    arch = OUT / "ring6d.arch.json"
    exp = OUT / "ring6d.expanded.json"
    if not arch.exists():
        m = Machine.ring(3, 2, 2, name="ring6d")
        arch.write_text(json.dumps(m.arch.to_json(), indent=1) + "\n", encoding="utf-8", newline="\n")
    rc, log, _ = sh([sys.executable, BRIDGE / "export_arch.py", arch, "-o", exp])
    if rc != 0:
        raise SystemExit("export_arch failed:\n" + log[-800:])
    return arch, exp


def compile_one(gid: str, body: str, title: str, note: str, arch: Path, exp: Path) -> dict:
    d = OUT / gid
    d.mkdir(parents=True, exist_ok=True)
    qasm = d / "circuit.qasm"
    qasm.write_text(HEADER + body + "\n", encoding="utf-8", newline="\n")
    prefix = d / "prog"
    rc, log, secs = sh([QCCDC, "compile", qasm, "--arch", exp, "-o", prefix])
    if rc != 0 or not (d / "prog.tsir.json").exists():
        raise SystemExit(f"{gid}: the compiler refused:\n{log[-800:]}")
    summary = next((ln.strip() for ln in log.splitlines() if "instructions" in ln and "layers" in ln), "")
    cooled = d / "prog.cooled.tsir.json"
    rc, clog, _ = sh([sys.executable, BRIDGE / "insert_cooling.py", d / "prog.tsir.json", "--arch", arch, "-o", cooled])
    if rc != 0:
        raise SystemExit(f"{gid}: cooling failed:\n{clog[-800:]}")
    rc, rlog, _ = sh([sys.executable, BRIDGE / "check_tsir.py", cooled, "--arch", arch, "--model", "corrected", "--json"])
    rules = json.loads(rlog[rlog.index("{"):]) if "{" in rlog else {"error": rlog[-400:]}
    (d / "rules.json").write_text(json.dumps(rules, indent=1) + "\n", encoding="utf-8", newline="\n")
    sh([sys.executable, BRIDGE / "mk_qcheck_input.py", prefix, "--arch", exp, "-o", d / "prog.qcheck.json"])
    rc, vlog, lsecs = sh([sys.executable, BRIDGE / "check_cert.py", prefix, "--qasm", qasm, "--arch", arch,
                          "--qcheck", d / "prog.qcheck.json", "--json"], timeout=1800)
    verdict = json.loads(vlog[vlog.index("{"):]) if "{" in vlog else {"error": vlog[-400:]}
    (d / "verdict.json").write_text(json.dumps(verdict, indent=1) + "\n", encoding="utf-8", newline="\n")
    meta = {"id": gid, "title": title, "note": note, "compiler": summary,
            "compile_seconds": round(secs, 2), "check_seconds": round(lsecs, 1)}
    (d / "meta.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"  {gid:8s} {summary[:70]:70s} rules {len(rules.get('passed', []))} passed, "
          f"{len(rules.get('failed', []))} failed | R10 {verdict.get('R10')} ({lsecs:.0f} s)", flush=True)
    return meta


def load_compiled() -> list[dict]:
    """What the page reads: every example's files, in GATES order; empty if none exist."""
    out = []
    for gid, body, title, note in GATES:
        d = OUT / gid
        if not (d / "verdict.json").exists():
            continue
        rd = lambda name: json.loads((d / name).read_text(encoding="utf-8"))
        out.append({"id": gid, "dir": d, "qasm": (d / "circuit.qasm").read_text(encoding="utf-8"),
                    "meta": rd("meta.json"), "rules": rd("rules.json"), "verdict": rd("verdict.json"),
                    "cert": rd("prog.qcert.json"), "tsir": rd("prog.cooled.tsir.json"),
                    "raw": rd("prog.tsir.json")})
    return out


def main(argv=None) -> int:
    for exe in (QCCDC, QCHECK):
        if not exe.exists():
            raise SystemExit(f"{exe} is not built; see Compiler/README.md")
    arch, exp = device()
    print(f"device {arch.name}; {len(GATES)} circuits")
    for gid, body, title, note in GATES:
        compile_one(gid, body, title, note, arch, exp)
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
