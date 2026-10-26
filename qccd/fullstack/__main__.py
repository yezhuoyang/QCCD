"""`python -m qccd.fullstack run PROG.prog --machine M --sysspec K --arch A -o DIR`

Compile one FormalRV program through every layer onto a QCCD device and write DIR/trace.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m qccd.fullstack")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="one program, every layer, onto a QCCD device")
    r.add_argument("prog", type=Path)
    r.add_argument("--machine", type=Path, required=True, help="the FormalRV machine profile")
    r.add_argument("--sysspec", type=Path, required=True, help="FormalRV system contracts")
    r.add_argument("--arch", type=Path, required=True, help="the QCCD device (.arch.json)")
    r.add_argument("-o", "--out", type=Path, required=True)
    r.add_argument("--code", default="surface")
    r.add_argument("--distance", type=int, default=3)
    r.add_argument("--specialize", action="append", default=[], help="NAME=VALUE (FormalRV --specialize)")
    r.add_argument("--physics", default="physics@2")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        from .pipeline import run
        spec = dict(s.split("=", 1) for s in a.specialize)
        t = run(a.prog, machine=a.machine, sysspec=a.sysspec, arch=a.arch, out=a.out, code=a.code,
                distance=a.distance, specialize=spec, physics=a.physics)
        print(json.dumps({k: t[k] for k in ("program", "abstract", "concrete", "physical_qubits", "total_s")}, indent=1))
        c = t["checks"]
        print("rules:", len(c["rules"]["passed"]), "passed,", c["rules"]["failed"] or "none", "failed;",
              "correspondence", c["correspondence"]["ok"], "; R10 semantic", c["r10_semantic"]["o2_semantics"],
              c["r10_semantic"]["order"], "; R10 Lean", c["r10_lean"].get("status"))
        s = c["stack"]
        print(f"stack: {s['instructions_checked']} instructions, {s['event_chains_resolved']} chains through "
              f"every layer, {'ok' if s['ok'] else 'PROBLEMS: ' + '; '.join(s['problems'][:3])}")
        for s in t["steps"]:
            print(f"  {s['s']:8.2f} s  {s['step']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
