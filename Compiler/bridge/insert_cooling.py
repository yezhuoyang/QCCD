"""Pass 8 -- cooling insertion, by calling the pass that already exists.

`qccd/compile/cooling.py` inserts the cooling a program needs to satisfy R7 and R7c, and
it does something better than "works on the cases tried": its R7 trigger **provably
converges in one pass**, because a global cool zeroes every ion, so inserting one
strictly lowers n-bar everywhere downstream -- and the pass asserts that on a re-replay
rather than assuming it.

Reimplementing that in OCaml would buy nothing and would create a second opinion about
when an ion is too hot to gate.  `Compiler/PLAN.md` says of this pass: *call it, do not
rewrite it*.  This is the call.

    python Compiler/bridge/insert_cooling.py build/out/ghz8_grid9x9.tsir.json \
        --arch arch/grid9x9.arch.json -o build/out/ghz8_grid9x9.cooled.tsir.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qccd.arch import Architecture  # noqa: E402
from qccd.compile.cooling import CoolingPolicy, insert_cooling  # noqa: E402
from qccd.cost.models import corrected_model  # noqa: E402
from qccd.ir.tsir import TSIR  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("program")
    ap.add_argument("--arch", required=True)
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--table", default="qccdsim_jones")
    ap.add_argument("--max-gate-quanta", type=float, default=None,
                    help="override the architecture's R7 budget (a sweep axis)")
    args = ap.parse_args(argv)

    arch_path = ROOT / args.arch if not Path(args.arch).is_absolute() else Path(args.arch)
    arch = Architecture.from_json(json.loads(arch_path.read_text(encoding="utf-8")))
    prog = TSIR.load(args.program)
    model = corrected_model(args.table)

    policy = CoolingPolicy()
    if args.max_gate_quanta is not None:
        policy = CoolingPolicy(max_gate_quanta=args.max_gate_quanta)

    r = insert_cooling(prog, arch, model, policy=policy)
    r.program.save(args.out, indent=None)

    share = 100 * r.cooling_share
    print(f"{prog.name}: {len(prog)} -> {len(r.program)} instructions")
    print(f"  {r.n_cools} global cooling operations, {r.cooling_us / 1000:.2f} ms "
          f"({share:.1f}% of runtime)")
    print(f"  R7 violations {r.r7_violations_before} -> {r.r7_violations_after}")
    print(f"  -> {args.out}")
    # The pass is meant to converge; if it did not, saying so is the whole point of
    # having a number for it.
    return 0 if r.r7_violations_after == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
