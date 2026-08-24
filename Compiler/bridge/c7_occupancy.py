"""C7: at what occupancy does individual-ion routing stop working on a closed loop?

`qccd/compile/pipeline.py` compiles BB[[144,12,12]] onto `ring144_24v` and this compiler
cannot -- not because of a bug, but because of a difference in vocabulary that is worth
measuring rather than asserting.

The specialised pipeline moves the loop **rigidly**: one `loop_shift` template advances
every ion at once, so occupancy never changes and there is no such thing as a blocked
path.  That is the platform's whole thesis (`docs/PLAN.md` §1 -- rotation needs exactly
one movement template where an odd-even sort needs many).

This compiler routes ions **individually**, which is what lets it target a grid, a ladder
and a racetrack from the same code.  On a closed loop at high occupancy that vocabulary
runs out: every hop needs a free slot at its destination, and when there are none, there
is nowhere to go.

So the honest comparison is not "which is faster" -- it is *where the general router's
range ends*, and that is a number.  This sweeps the qubit count on a device and reports
the last one that compiles.

    python Compiler/bridge/c7_occupancy.py --device ring144_24v
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
OCAML_ENV = {
    **os.environ,
    "PATH": os.path.expanduser(r"~\AppData\Local\opam\default\bin")
    + os.pathsep + os.environ.get("PATH", ""),
}
EXE = COMPILER / "ocaml" / "_build" / "default" / "bin" / "qccdc_cli.exe"


def esm_like(n_data: int, n_anc: int, weight: int = 6) -> str:
    """A syndrome-extraction round of the same SHAPE as BB144's, at a chosen size.

    Same structure as `gen_bb144.py` emits -- ancillas reused via measure/reset, checks of
    a fixed weight -- so what the sweep varies is occupancy and nothing else.
    """
    n = n_data + n_anc
    L = ["OPENQASM 2.0;", 'include "qelib1.inc";', f"qreg q[{n}];",
         f"creg c[{n_anc}];"]
    checks = max(n_anc, n_data // 2)
    for k in range(checks):
        anc = n_data + (k % n_anc)
        if k >= n_anc:
            L.append(f"reset q[{anc}];")
        L.append(f"h q[{anc}];")
        for j in range(weight):
            L.append(f"cx q[{anc}],q[{(k * weight + j * 7) % n_data}];")
        L.append(f"h q[{anc}];")
        L.append(f"measure q[{anc}] -> c[{k % n_anc}];")
    return "\n".join(L) + "\n"


def try_compile(qasm: Path, device: str, out: Path, timeout: float) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            [str(EXE), "compile", str(qasm),
             "--arch", str(COMPILER / "build" / f"{device}.expanded.json"),
             "-o", str(out)],
            capture_output=True, text=True, env=OCAML_ENV, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "timed out"
    if r.returncode != 0:
        last = (r.stderr or r.stdout).strip().splitlines()
        msg = last[-1] if last else "failed"
        # the compiler prefixes its errors with the input path; the path is not the news
        if ": " in msg:
            msg = msg.split(": ", 1)[1]
        return False, msg[:64]
    cyc = "?"
    for line in r.stdout.splitlines():
        if "transport cycles" in line:
            cyc = line.split("layers,")[1].split("transport")[0].strip()
    return True, f"{cyc} cycles"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--device", default="ring144_24v")
    ap.add_argument("--ancillas", type=int, default=24)
    ap.add_argument("--sizes", default="24,48,72,96,120,144,168")
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    arch = json.loads(
        (COMPILER / "build" / f"{args.device}.expanded.json").read_text(encoding="utf-8"))
    caps = arch["node_caps"]
    traps = [n for n, c in caps.items() if c.get("degree", 0) > 0]
    total_slots = sum(
        min(caps[n]["capacity"], 1) if caps[n]["is_junction"] else caps[n]["capacity"]
        for n in traps)

    print(f"{args.device}: {len(traps)} traps, {total_slots} ion slots "
          f"(a degree->=3 node holds 1, whatever its zone declares)")
    print(f"{'qubits':>7} {'occupancy':>10}  result")
    print("-" * 46)

    rows = []
    tmp = COMPILER / "build" / "c7"
    tmp.mkdir(parents=True, exist_ok=True)
    for total in [int(x) for x in args.sizes.split(",")]:
        n_anc = min(args.ancillas, max(1, total // 4))
        n_data = total - n_anc
        if n_data < 2:
            continue
        q = tmp / f"esm_{total}.qasm"
        q.write_text(esm_like(n_data, n_anc), encoding="utf-8")
        ok, msg = try_compile(q, args.device, tmp / f"esm_{total}", args.timeout)
        occ = 100.0 * total / total_slots
        print(f"{total:>7} {occ:>9.1f}%  {'compiled -- ' + msg if ok else msg}")
        rows.append({"qubits": total, "occupancy_pct": occ, "ok": ok, "detail": msg})

    good = [r for r in rows if r["ok"]]
    print()
    if good:
        last = max(good, key=lambda r: r["qubits"])
        print(f"the individual-ion router reaches {last['qubits']} qubits "
              f"({last['occupancy_pct']:.1f}% occupancy) on {args.device}")
        bad = [r for r in rows if not r["ok"] and r["qubits"] > last["qubits"]]
        if bad:
            first = min(bad, key=lambda r: r["qubits"])
            print(f"and stops at {first['qubits']} ({first['occupancy_pct']:.1f}%): "
                  f"{first['detail']}")
    else:
        print(f"no size compiled on {args.device}")
    print()
    print("A rigid-rotation router has no such limit: one template moves every ion at")
    print("once, so occupancy never changes and no path is ever blocked.  That is what")
    print("`qccd/compile/pipeline.py` uses, and why it compiles BB[[144,12,12]] here.")

    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
