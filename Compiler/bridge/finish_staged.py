"""Finish native compilation with checked physical stages and artifact acceptance."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qccd.compile.driver import finish_compilation
from qccd.workspace.evaluator import Toolchain


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("prefix")
    ap.add_argument("--qasm", required=True)
    ap.add_argument("--arch", required=True)
    ap.add_argument("--expanded-arch", required=True)
    ap.add_argument("--table", default="qccdsim_jones")
    ap.add_argument("--objective", choices=("fastest", "coolest", "balanced"), default="fastest")
    ap.add_argument("--qccdc", type=Path)
    ap.add_argument("--qcheck", type=Path)
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--semantic-timeout", type=float, default=None)
    ap.add_argument("--semantic-mem-mb", type=int, default=12288)
    args = ap.parse_args(argv)
    tc = Toolchain.discover()
    if args.qccdc is not None:
        tc.qccdc = args.qccdc
    if args.qcheck is not None:
        tc.qcheck = args.qcheck
    result = finish_compilation(args.prefix, qasm=args.qasm, arch_path=args.arch,
                                expanded_arch_path=args.expanded_arch, toolchain=tc,
                                table=args.table, objective=args.objective, timeout=args.timeout,
                                semantic_timeout=args.semantic_timeout, semantic_mem_mb=args.semantic_mem_mb)
    print(json.dumps(result, indent=2))
    return 0 if result["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
