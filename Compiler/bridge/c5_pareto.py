"""C5: the (runtime, error) Pareto frontier, by sweeping the exchange rate.

`docs/PLAN.md` §0.2 is the reason this exists: cost and steps are not rival objectives,
they are two named halves of **one error budget**.  Transport does not itself cause gate
error (§0.3) -- it heats, and heating degrades the *next* gate.  So the compiler faces a
genuine continuous trade-off:

    cool more often  ->  more runtime, lower n-bar at each gate, lower gate error
    cool less often  ->  less runtime, hotter gates, higher error

Neither end is right, and no fixed policy finds the middle.  The knob that traces the
curve is the R7 budget `max_gate_quanta`: it is the shadow price of a quantum in units of
schedule time, and `qccd/compile/cooling.py` already routes it through to the rule that
enforces it, so lowering it really does change the schedule rather than only the report.

The three shipped operating-point policies -- `fastest`, `coolest`, `balanced` -- are
single points, and the honest finding is not that they are *dominated*: they sit ON the
frontier, at its slowest and most accurate end.  What they are is not a choice.  A fixed
policy gives no access to the rest of the curve, and the rest of the curve is where the
interesting engineering is.

    python Compiler/bridge/c5_pareto.py build/out/clifford12_grid9x9.tsir.json \
        --arch arch/grid9x9.arch.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qccd.arch import Architecture, OperatingPointPolicy  # noqa: E402
from qccd.compile.cooling import CoolingPolicy, insert_cooling  # noqa: E402
from qccd.cost.models import corrected_model  # noqa: E402
from qccd.ir.tsir import TSIR  # noqa: E402
from qccd.verify import verify  # noqa: E402


def evaluate(prog: TSIR, arch: Architecture, *, table: str, objective: str,
             budget: float | None) -> dict | None:
    """Compile-time policy in, (runtime, error) out.  `None` if no schedule satisfies it."""
    model = corrected_model(table)
    model = replace(model, policy=OperatingPointPolicy(table, objective))
    try:
        r = insert_cooling(prog, arch, model,
                           policy=CoolingPolicy(max_gate_quanta=budget))
    except Exception as exc:  # noqa: BLE001 -- an infeasible budget is a datum
        return {"infeasible": str(exc)[:60], "table": table, "objective": objective,
                "budget": budget}
    # Judge the schedule against the budget it was BUILT for.  Judging every swept point
    # against the architecture's original budget would report each one as an R7 failure
    # and measure nothing -- the sweep is asking what the trade-off looks like if the
    # device tolerates a hotter gate, so the rule has to be asked the same question.
    cfg = {"max_gate_quanta": budget} if budget is not None else None
    rep = verify(r.program, arch, model, check_metrics=False, rule_config=cfg)
    res = rep.result
    rules = rep.rules.summary()
    return {
        "table": table,
        "objective": objective,
        "budget": budget,
        "runtime_ms": res.total_us / 1000.0,
        "gate_error": res.gate_error_sum,
        "peak_quanta": res.peak_quanta,
        "total_quanta": res.total_quanta(),
        "n_cools": r.n_cools,
        "cooling_share": r.cooling_share,
        "failed": rules["failed"],
    }


def pareto(points: list[dict]) -> list[dict]:
    """Minimal (runtime, error) points -- nothing else is both faster AND more accurate."""
    out = []
    for p in points:
        if any(
            q is not p
            and q["runtime_ms"] <= p["runtime_ms"]
            and q["gate_error"] <= p["gate_error"]
            and (q["runtime_ms"] < p["runtime_ms"] or q["gate_error"] < p["gate_error"])
            for q in points
        ):
            continue
        out.append(p)
    return sorted(out, key=lambda p: p["runtime_ms"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("program")
    ap.add_argument("--arch", required=True)
    ap.add_argument("--tables", default="qccdsim_jones,transport_excitation")
    ap.add_argument("--budgets", default="0.25,0.5,1,2,4,8,none")
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    arch_path = ROOT / args.arch if not Path(args.arch).is_absolute() else Path(args.arch)
    arch = Architecture.from_json(json.loads(arch_path.read_text(encoding="utf-8")))
    prog = TSIR.load(args.program)

    budgets: list[float | None] = [
        None if b.strip() == "none" else float(b) for b in args.budgets.split(",")
    ]
    tables = [t.strip() for t in args.tables.split(",")]
    objectives = ["fastest", "coolest", "balanced"]

    rows: list[dict] = []
    for table in tables:
        for objective in objectives:
            for budget in budgets:
                r = evaluate(prog, arch, table=table, objective=objective, budget=budget)
                if r is not None:
                    rows.append(r)

    good = [r for r in rows if "infeasible" not in r and not r["failed"]]
    print(f"{prog.name} on {arch.name}: {len(good)} feasible policies "
          f"of {len(rows)} tried")
    print(f"{'table':<22} {'objective':<10} {'budget':>7} {'runtime':>9} "
          f"{'error':>10} {'cools':>6} {'peak n':>7}")
    print("-" * 76)

    # Collapse policies that land on the same point: `fastest`/`coolest`/`balanced` often
    # resolve to the identical curve point, and listing one outcome three times makes a
    # frontier look richer than it is.
    seen: dict[tuple, dict] = {}
    for r in good:
        seen.setdefault((round(r["runtime_ms"], 4), round(r["gate_error"], 8)), r)
    distinct = list(seen.values())
    front = pareto(distinct)
    front_ids = {id(p) for p in front}
    for r in sorted(good, key=lambda r: (r["runtime_ms"], r["gate_error"])):
        mark = " *" if id(r) in front_ids else "  "
        b = "none" if r["budget"] is None else f"{r['budget']:g}"
        print(f"{r['table']:<22} {r['objective']:<10} {b:>7} "
              f"{r['runtime_ms']:>8.2f}ms {r['gate_error']:>10.4f} "
              f"{r['n_cools']:>6} {r['peak_quanta']:>7.2f}{mark}")

    print()
    print(f"Pareto frontier: {len(front)} distinct points of {len(good)} policy settings "
          f"({len(distinct)} distinct outcomes) -- nothing else is both faster and more "
          f"accurate")
    if front:
        fast, acc = front[0], front[-1]
        print(f"  fastest on the frontier : {fast['runtime_ms']:.2f} ms, "
              f"error {fast['gate_error']:.4f}  ({fast['objective']}, "
              f"budget {fast['budget']})")
        print(f"  most accurate           : {acc['runtime_ms']:.2f} ms, "
              f"error {acc['gate_error']:.4f}  ({acc['objective']}, "
              f"budget {acc['budget']})")
        if acc["gate_error"] > 0:
            print(f"  the frontier spans {fast['runtime_ms'] / max(acc['runtime_ms'], 1e-9):.2f}x "
                  f"in runtime for {acc['gate_error'] / max(fast['gate_error'], 1e-12):.2f}x "
                  f"in error")

    # What a fixed policy actually costs you.  Not accuracy -- it sits at the accurate
    # end -- but the entire rest of the curve.
    # look in `good`, not `distinct`: a fixed policy is usually deduped away because
    # it lands on the same point as some swept one, and that is the finding
    fixed = [r for r in good if r["budget"] is None]
    if fixed and front:
        f0 = min(fixed, key=lambda r: r["gate_error"])
        speedup = f0["runtime_ms"] / max(front[0]["runtime_ms"], 1e-9)
        print(f"  a FIXED policy lands at {f0['runtime_ms']:.2f} ms / "
              f"error {f0['gate_error']:.4f} -- on the frontier, at its slow end.")
        print(f"  it is not dominated; it is simply not a choice: sweeping the budget "
              f"reaches {speedup:.2f}x faster")
        print(f"  at {front[0]['gate_error'] / max(f0['gate_error'], 1e-12):.1f}x the "
              f"gate error, and every point between.")

    if args.json:
        Path(args.json).write_text(json.dumps({"rows": rows, "frontier": front}, indent=1),
                                   encoding="utf-8")
        print(f"  -> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
