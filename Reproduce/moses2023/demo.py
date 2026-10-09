"""A circuit of our own, compiled on the rebuilt H2, for the website's H2 Studio page.

The paper's benchmark circuits are all rights reserved, so no programme compiled from them
is published.  This one is ours: the textbook GHZ state on 32 qubits, built as a binary
tree (depth 5, 31 CX) -- the shape of the paper's own GHZ benchmark, written here from the
definition.  It is compiled by the published compiler on `h2.arch.json`, judged by every
rule, and its certificate is checked by the Lean checker; the cooled programme and a summary
are written to `demo/`, which the site build reads.

    set QCCD_QCCDC=<the published qccdc_cli>
    python Reproduce/moses2023/demo.py <work dir>
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from qccd.cost import corrected_model  # noqa: E402
from qccd.qec import compile_with_qccdc, load_arch, load_program  # noqa: E402
from qccd.verify import verify  # noqa: E402

ARCH = HERE / "h2.arch.json"
OUT = HERE / "demo"
N = 32


def ghz_tree(n: int) -> str:
    """H on q0, then every qubit that holds the state copies it to one that does not, doubling
    each layer: 1 -> 2 -> 4 -> ... -> n (depth log2 n)."""
    lines = ["OPENQASM 2.0;", 'include "qelib1.inc";', f"qreg q[{n}];", f"creg c[{n}];", "h q[0];"]
    have, step = [0], n // 2
    while step >= 1:
        for q in list(have):
            if q + step < n:
                lines.append(f"cx q[{q}],q[{q + step}];")
                have.append(q + step)
        step //= 2
    lines += [f"measure q[{i}] -> c[{i}];" for i in range(n)]
    return "\n".join(lines) + "\n"


def release_of(exe: Path) -> str | None:
    import hashlib
    man = json.loads((ROOT / "qccd" / "workspace" / "toolchain.json").read_text(encoding="utf-8"))
    d = hashlib.sha256(exe.read_bytes()).hexdigest()
    q = man["qccdc_cli"]
    return q["version"] if any(a.get("sha256") == d for a in q["assets"].values()) else None


def main(argv: list[str]) -> int:
    work = Path(argv[0]).resolve()
    qasm = ghz_tree(N)
    prog_path, cert_path = compile_with_qccdc(qasm, ARCH, work, mode="compile", timeout=900)
    arch = load_arch(ARCH)
    prog = load_program(prog_path)
    rep = verify(prog, arch, corrected_model(table="local"))
    failed = sorted(r for r, n in rep.rules.by_rule().items() if n)
    # the trusted checker, on the certificate (mk_qcheck_input reads <prefix>.qcert.json)
    prefix = str(cert_path)[: -len(".qcert.json")]
    qin = work / "ghz.qin.json"
    subprocess.run([sys.executable, str(ROOT / "Compiler" / "bridge" / "mk_qcheck_input.py"), prefix,
                    "--arch", str(work / "device.expanded.json"), "-o", str(qin)],
                   check=True, capture_output=True)
    qc = subprocess.run([str(ROOT / "Compiler" / "lean" / ".lake" / "build" / "bin" / "qcheck.exe"), str(qin)],
                        capture_output=True, text=True, timeout=3600)
    lean = "ACCEPTED" if "ACCEPTED" in qc.stdout else "REJECTED"
    doc = json.loads(Path(prog_path).read_text(encoding="utf-8"))
    ins = doc.get("instructions", [])
    rounds = sum(1 for i in ins if i.get("type") == "gate" and any(len(p) == 2 for p in (i.get("pairs") or [])))
    OUT.mkdir(exist_ok=True)
    (OUT / "ghz32.qasm").write_text(qasm, encoding="utf-8", newline="\n")
    (OUT / "ghz32.tsir.json").write_text(json.dumps(doc), encoding="utf-8", newline="\n")
    # the certificate, so the page can join each instruction to its circuit statement
    (OUT / "ghz32.qcert.json").write_text(Path(cert_path).read_text(encoding="utf-8"),
                                          encoding="utf-8", newline="\n")
    summary = {"circuit": "GHZ on 32 qubits, binary tree (depth 5, 31 CX), written from the definition",
               "compiler": release_of(Path(os.environ["QCCD_QCCDC"])) or "an unreleased build",
               "instructions": len(ins), "rounds": rounds, "rules_failed": failed,
               "lean": lean}
    (OUT / "ghz32.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(summary))
    return 0 if not failed and lean == "ACCEPTED" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
