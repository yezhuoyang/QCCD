"""C1: run both front ends over the whole corpus and compare every circuit.

Written in Python rather than as a shell loop for one reason: 508 circuits x 2 parsers x
a diff is slow if each step is a process launch, and the qiskit side dominates.  Keeping
the oracle in-process and shelling out only to the OCaml binary cuts the run from minutes
to seconds, which is the difference between a test that gets run and one that does not.

    python Compiler/bridge/run_c1.py            # the whole corpus
    python Compiler/bridge/run_c1.py --only broadcast_zoo
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMPILER = HERE.parent

sys.path.insert(0, str(HERE))
from diff_dag import cmp_ops, cmp_wires  # noqa: E402
from qasm_oracle import to_canonical  # noqa: E402

from qiskit import qasm2  # noqa: E402

OCAML_ENV = {
    **os.environ,
    "PATH": os.path.expanduser(r"~\AppData\Local\opam\default\bin")
    + os.pathsep
    + os.environ.get("PATH", ""),
}
EXE = COMPILER / "ocaml" / "_build" / "default" / "bin" / "qccdc_cli.exe"


def ocaml_parse(qasm: Path, out: Path) -> tuple[bool, str]:
    r = subprocess.run(
        [str(EXE), "parse", str(qasm), "-o", str(out)],
        capture_output=True, text=True, env=OCAML_ENV)
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def compare(ours: dict, oracle: dict, limit: int = 6) -> list[str]:
    problems: list[str] = []
    for k in ("n_qubits", "n_clbits", "qregs", "cregs"):
        if ours.get(k) != oracle.get(k):
            problems.append(f"{k}: ours {ours.get(k)}, oracle {oracle.get(k)}")
    if problems:
        return problems
    problems = cmp_ops(ours, oracle, limit)
    if problems:
        return problems
    problems = cmp_wires(ours, oracle, limit)
    if problems:
        return problems
    a = sorted(tuple(e) for e in ours["edges"])
    b = sorted(tuple(e) for e in oracle["edges"])
    if a != b:
        only_a = [e for e in a if e not in set(b)][:4]
        only_b = [e for e in b if e not in set(a)][:4]
        problems.append(f"edges differ: only-ours {only_a}, only-oracle {only_b}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bench", default=str(COMPILER / "bench"))
    ap.add_argument("--only", default=None, help="run one circuit by stem")
    ap.add_argument("--max-report", type=int, default=8)
    args = ap.parse_args(argv)

    if not EXE.exists():
        print(f"missing {EXE}; run `cd Compiler/ocaml && dune build` first")
        return 2

    bench = Path(args.bench)
    files = sorted(bench.glob("*.qasm")) + sorted((bench / "random").glob("*.qasm"))
    if args.only:
        files = [f for f in files if f.stem == args.only]
        if not files:
            print(f"no circuit named {args.only!r}")
            return 2

    tmp = Path(tempfile.mkdtemp(prefix="c1_"))
    ok = fail = 0
    failures: list[tuple[str, list[str]]] = []
    total_ops = total_edges = 0

    for f in files:
        out = tmp / (f.stem + ".json")
        good, msg = ocaml_parse(f, out)
        if not good:
            fail += 1
            failures.append((f.name, [f"OCaml refused it: {msg}"]))
            continue
        try:
            circuit = qasm2.load(str(f),
                                 custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)
            oracle = to_canonical(circuit, f.stem)
        except Exception as exc:  # noqa: BLE001
            fail += 1
            failures.append((f.name, [f"oracle refused it: {type(exc).__name__}: {exc}"]))
            continue
        ours = json.loads(out.read_text(encoding="utf-8"))
        problems = compare(ours, oracle)
        if problems:
            fail += 1
            failures.append((f.name, problems))
        else:
            ok += 1
            total_ops += len(ours["ops"])
            total_edges += len(ours["edges"])

    print(f"C1 differential: {ok}/{len(files)} circuits match "
          f"({total_ops:,} ops, {total_edges:,} DAG edges agreed)")
    for name, problems in failures[: args.max_report]:
        print(f"\n  {name}")
        for p in problems[:6]:
            print(f"    {p}")
    if len(failures) > args.max_report:
        print(f"\n  ... and {len(failures) - args.max_report} more")

    print("\nC1 PASS" if fail == 0 else f"\nC1 FAIL ({fail} circuits)")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
