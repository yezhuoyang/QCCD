"""Replay a TSIR program and report every rule -- the acceptance gate for every milestone.

This is the compiler's contact with the evaluator.  Whatever produced the program --
Python, OCaml, a round-trip, a SAT model -- it is judged here, by the same
`qccd.verify.verify` the rest of the platform uses.  There is deliberately no separate
"compiler test harness": a program the compiler likes and the verifier rejects is a
rejected program.

    python Compiler/bridge/check_tsir.py Compiler/build/deck24.tsir.json \
        --arch arch/ring144_24v.arch.json --model deck \
        --expect-cost 397184 --expect-steps 8808

Exits non-zero if a rule fails or an expectation misses, so it drops straight into CI.
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
from qccd.cost.models import corrected_model, deck_model  # noqa: E402
from qccd.ir.tsir import TSIR, validate_program  # noqa: E402
from qccd.verify import verify  # noqa: E402

MODELS = {"deck": deck_model, "corrected": corrected_model}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("program", help="a .tsir.json")
    ap.add_argument("--arch", default="arch/ring144_24v.arch.json")
    ap.add_argument("--model", default="deck", choices=sorted(MODELS))
    ap.add_argument("--expect-cost", type=float, default=None)
    ap.add_argument("--expect-steps", type=int, default=None)
    ap.add_argument("--check-metrics", action="store_true",
                    help="also check the program's own claimed metrics (R9)")
    ap.add_argument("--json", action="store_true", help="machine-readable summary")
    args = ap.parse_args(argv)

    prog = TSIR.load(args.program)

    # Shape first.  A structurally invalid program replayed anyway produces a number,
    # and a number from an invalid program is worse than an error.
    shape = validate_program(prog)
    if shape:
        print("SHAPE ERRORS:")
        for e in shape:
            print(f"  {e}")
        return 2

    arch_path = ROOT / args.arch if not Path(args.arch).is_absolute() else Path(args.arch)
    arch = Architecture.from_json(json.loads(arch_path.read_text(encoding="utf-8")))
    model = MODELS[args.model]()

    rep = verify(prog, arch, model, check_metrics=args.check_metrics)
    res, rules = rep.result, rep.rules.summary()

    out = {
        "program": prog.name,
        "instructions": len(prog),
        "arch": arch.name,
        "model": model.name,
        "total_cost": res.total_cost,
        "total_steps": res.total_steps,
        "runtime_us": res.total_us,
        "contacts": res.n_gate_pairs,
        "templates": prog.templates(),
        "passed": rules["passed"],
        "failed": rules["failed"],
        "skipped": rules["skipped"],
        "partial": rules.get("partial", {}),
    }

    if args.json:
        print(json.dumps(out, indent=1, default=str))
    else:
        print(f"{prog.name}  ({len(prog)} instructions on {arch.name}, model {model.name})")
        print(f"  cost    {res.total_cost:,.0f}")
        print(f"  steps   {res.total_steps:,}")
        print(f"  runtime {res.total_us / 1000:,.2f} ms")
        print(f"  contacts {res.n_gate_pairs}")
        print(f"  rules passed  ({len(rules['passed'])}): {' '.join(rules['passed'])}")
        if rules.get("partial"):
            print(f"  rules partial ({len(rules['partial'])}): {' '.join(rules['partial'])}")
        for r, why in sorted(rules["skipped"].items()):
            print(f"  skipped {r}: {why}")
        if rules["failed"]:
            print(f"  RULES FAILED: {' '.join(rules['failed'])}")
            for v in rep.rules.violations[:10]:
                print(f"    {v}")

    bad = 0
    if rules["failed"]:
        bad = 1
    if args.expect_cost is not None and abs(res.total_cost - args.expect_cost) > 1e-6:
        print(f"  EXPECTED cost {args.expect_cost:,.0f}, got {res.total_cost:,.0f}")
        bad = 1
    if args.expect_steps is not None and res.total_steps != args.expect_steps:
        print(f"  EXPECTED steps {args.expect_steps:,}, got {res.total_steps:,}")
        bad = 1
    if not bad and (args.expect_cost is not None or args.expect_steps is not None):
        print("  EXPECTATIONS MET")
    return bad


if __name__ == "__main__":
    raise SystemExit(main())
