"""The reference compiler under the `qccd.compiler@1` contract: qccdc, then cooling.

    python -m qccd.bench.baseline --circuit C --device D --expanded E --out DIR

It is the same path a design's own compile takes (`results._job_compile`): on a device with a
closed loop, qccdc's rigid rotation first and its router if rotation does not apply; else
the router; then `insert_cooling.py`.  It is the Compiler board's reference row, the
denominator of every speedup, and the starting point `qccd bench init-compiler` writes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .contract import REFUSED_EXIT


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m qccd.bench.baseline", description=__doc__.splitlines()[0])
    ap.add_argument("--circuit", required=True)
    ap.add_argument("--device", required=True)
    ap.add_argument("--expanded", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-cooling", action="store_true", help="skip the cooling pass (for comparison)")
    a = ap.parse_args(argv)
    from ..arch import Architecture
    from ..compile.programs import closed_loops
    from ..workspace.evaluator import Toolchain
    tc = Toolchain.discover()
    if tc.qccdc is None:
        print("qccdc_cli is not installed: `qccd toolchain install`, or build Compiler/ocaml", file=sys.stderr)
        return 2
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    arch = Architecture.from_json(json.loads(Path(a.device).read_text(encoding="utf-8")))
    modes = ["rotate", "compile"] if closed_loops(arch) else ["compile"]
    prefix, why = out / "qccdc", ""
    for mode in modes:
        for stale in (".tsir.json", ".qcert.json"):
            Path(str(prefix) + stale).unlink(missing_ok=True)
        cp = subprocess.run([str(tc.qccdc), mode, a.circuit, "--arch", a.expanded, "-o", str(prefix)],
                            capture_output=True, text=True)
        why = (cp.stdout + cp.stderr).strip().splitlines()[-1:] or [f"exit {cp.returncode}"]
        cert_p = Path(str(prefix) + ".qcert.json")
        if cp.returncode == 0 and cert_p.exists() and not json.loads(cert_p.read_text()).get("unrealised"):
            break
    else:
        print(f"qccdc cannot compile this pair: {why[0]}", file=sys.stderr)
        return REFUSED_EXIT
    prog_p = Path(str(prefix) + ".tsir.json")
    final = prog_p
    if not a.no_cooling:
        final = out / "qccdc.cooled.tsir.json"
        co = subprocess.run([tc.python, str(tc.bridge / "insert_cooling.py"), str(prog_p), "--arch", a.device,
                             "-o", str(final)], capture_output=True, text=True)
        if co.returncode != 0 or not final.exists():
            print(f"cooling failed: {(co.stdout + co.stderr)[-500:]}", file=sys.stderr)
            return 1
    cert = json.loads(cert_p.read_text(encoding="utf-8"))
    doc = json.loads(final.read_text(encoding="utf-8"))
    doc.setdefault("meta", {})["qubit_map"] = {str(q): ion for q, ion in cert["map"].items()}
    doc["meta"]["compiler"] = "qccdc (reference)"
    (out / "program.tsir.json").write_text(json.dumps(doc), encoding="utf-8")
    (out / "certificate.qcert.json").write_text(json.dumps(cert), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
