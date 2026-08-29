"""The verification matrix: every example on every architecture, checked end to end.

For each pair it runs the whole stack and records what came out:

    compile  ->  insert cooling  ->  every checkable rule  ->  R10 (proved Lean checker + tableau)

and reports one of:

    ok           compiled, every checkable rule passed, R10 `passed`
    partial      compiled and legal, but R10 could not be fully established
                 (outside the Clifford fragment, so the tableau says nothing)
    RULES        compiled but violates a hardware rule -- a compiler bug
    unroutable   the heuristic router gave up (a limitation, not a wrong answer)
    too-small    the device has fewer usable traps than the circuit has qubits
    unrealised   some circuit op could not be placed at all

The distinction between the last four is the whole point of the table.  A compiler that
reported them all as "failed" would hide which ones are bugs.

    python Compiler/bridge/run_matrix.py
    python Compiler/bridge/run_matrix.py --circuits steane_esm --devices grid9x9
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMPILER = HERE.parent
ROOT = COMPILER.parent

OCAML_ENV = {
    **os.environ,
    "PATH": os.path.expanduser(r"~\AppData\Local\opam\default\bin")
    + os.pathsep + os.environ.get("PATH", ""),
}
EXE = COMPILER / "ocaml" / "_build" / "default" / "bin" / "qccdc_cli.exe"
QCHECK = COMPILER / "lean" / ".lake" / "build" / "bin" / "qcheck.exe"

DEVICES = ["stationary_chain", "chain", "grid9x9", "deck_unit_cell", "ladder_2x72",
           "ring144_24v", "cyclone_base", "cyclone_dual_loop", "h2_racetrack"]


def sh(cmd: list[str]) -> tuple[int, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, env=OCAML_ENV)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def one(circuit: Path, device: str, outdir: Path) -> dict:
    stem = f"{circuit.stem}_{device}"
    prefix = outdir / stem
    res: dict = {"circuit": circuit.stem, "device": device}

    rc, out = sh([str(EXE), "compile", str(circuit),
                  "--arch", str(COMPILER / "build" / f"{device}.expanded.json"),
                  "-o", str(prefix)])
    if rc != 0:
        low = out.lower()
        res["status"] = ("too-small" if "usable traps" in low
                         else "unroutable" if "unroutable" in low
                         else "compile-error")
        res["detail"] = out.strip().splitlines()[-1][:70] if out.strip() else ""
        return res
    for line in out.splitlines():
        if "transport cycles" in line:
            res["cycles"] = int(line.split("layers,")[1].split("transport")[0])
    if "UNREALISED" in out:
        res["status"] = "unrealised"
        res["detail"] = next(l.strip() for l in out.splitlines() if "UNREALISED" in l)[:70]
        return res

    cooled = str(prefix) + ".cooled.tsir.json"
    sh([sys.executable, str(HERE / "insert_cooling.py"), str(prefix) + ".tsir.json",
        "--arch", f"arch/{device}.arch.json", "-o", cooled])

    rc, out = sh([sys.executable, str(HERE / "check_tsir.py"), cooled,
                  "--arch", f"arch/{device}.arch.json", "--model", "corrected"])
    for line in out.splitlines():
        if "rules passed" in line:
            res["rules"] = int(line.split("(")[1].split(")")[0])
        if line.strip().startswith("runtime"):
            res["ms"] = float(line.split()[1])
    if "RULES FAILED" in out:
        res["status"] = "RULES"
        res["detail"] = next(l for l in out.splitlines() if "RULES FAILED" in l).strip()[:70]
        return res

    qc = outdir / f"qcheck_{stem}.json"
    sh([sys.executable, str(HERE / "mk_qcheck_input.py"), str(prefix),
        "--arch", str(COMPILER / "build" / f"{device}.expanded.json"), "-o", str(qc)])
    cmd = [sys.executable, str(HERE / "check_cert.py"), str(prefix),
           "--qasm", str(circuit), "--arch", f"arch/{device}.arch.json"]
    if QCHECK.exists() and qc.exists():
        cmd += ["--qcheck", str(qc)]
    _, out = sh(cmd)
    verdict = "?"
    for line in out.splitlines():
        if "-> R10" in line:
            verdict = line.split("-> R10")[1].split(":")[0].strip()
    res["r10"] = verdict
    res["status"] = "ok" if verdict == "passed" else (
        "partial" if verdict == "partial" else "R10-FAILED")
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--examples", default=str(COMPILER / "examples"))
    ap.add_argument("--circuits", default=None, help="comma-separated stems")
    ap.add_argument("--devices", default=",".join(DEVICES))
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    if not EXE.exists():
        print(f"missing {EXE}; run `cd Compiler/ocaml && dune build`")
        return 2

    circuits = sorted(Path(args.examples).glob("*.qasm"))
    if args.circuits:
        want = set(args.circuits.split(","))
        circuits = [c for c in circuits if c.stem in want]
    devices = args.devices.split(",")
    outdir = COMPILER / "build" / "matrix"
    outdir.mkdir(parents=True, exist_ok=True)

    rows = []
    print(f"{'circuit':<15}" + "".join(f"{d[:11]:>13}" for d in devices))
    print("-" * (15 + 13 * len(devices)))
    for c in circuits:
        cells = []
        for d in devices:
            r = one(c, d, outdir)
            rows.append(r)
            if r["status"] == "ok":
                cells.append(f"{r.get('cycles', '?')}c/{r.get('rules', '?')}r")
            elif r["status"] == "partial":
                cells.append(f"~{r.get('cycles', '?')}c")
            else:
                cells.append({"unroutable": "unroutable", "too-small": "too-small",
                              "unrealised": "unrealised", "RULES": "RULES!",
                              "R10-FAILED": "R10!",
                              "compile-error": "error"}[r["status"]])
        print(f"{c.stem:<15}" + "".join(f"{x:>13}" for x in cells))

    n = len(rows)
    ok = sum(1 for r in rows if r["status"] == "ok")
    partial = sum(1 for r in rows if r["status"] == "partial")
    bugs = sum(1 for r in rows if r["status"] in ("RULES", "R10-FAILED", "compile-error"))
    limits = sum(1 for r in rows if r["status"] in ("unroutable", "too-small", "unrealised"))

    print()
    print(f"{n} (circuit, architecture) pairs")
    print(f"  {ok:>3} fully verified   -- compiled, all rules pass, R10 passed")
    print(f"  {partial:>3} partial         -- legal, R10 not fully established "
          f"(non-Clifford)")
    print(f"  {limits:>3} out of reach    -- device too small, or the heuristic router "
          f"declined")
    print(f"  {bugs:>3} DEFECTS         -- a rule violated or R10 refused")
    for r in rows:
        if r["status"] in ("RULES", "R10-FAILED", "compile-error"):
            print(f"      {r['circuit']} / {r['device']}: {r.get('detail', r['status'])}")

    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=1), encoding="utf-8")
        print(f"  -> {args.json}")
    return 1 if bugs else 0


if __name__ == "__main__":
    raise SystemExit(main())
