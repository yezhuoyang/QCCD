"""Compare the OCaml front end's circuit JSON against the qiskit oracle's.

Reports the *first* real disagreement with enough context to act on, rather than a wall
of derived differences: one wrong op index shifts every edge that mentions it, so
dumping the edge-set delta first would bury the cause under a hundred consequences.

The comparison order is therefore causal -- registers, then ops, then per-wire
sequences, then edges -- and it stops at the first category that disagrees.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(p: str) -> dict:
    return json.loads(Path(p).read_text(encoding="utf-8"))


def cmp_ops(a: dict, b: dict, limit: int) -> list[str]:
    out: list[str] = []
    oa, ob = a["ops"], b["ops"]
    if len(oa) != len(ob):
        out.append(f"op count: ours {len(oa)}, oracle {len(ob)}")
        # still show the first divergence, which is what says *where* they parted
    for i, (x, y) in enumerate(zip(oa, ob)):
        diffs = []
        for k in ("name", "qubits", "clbits", "cond"):
            if x.get(k) != y.get(k):
                diffs.append(f"{k} {x.get(k)!r} != {y.get(k)!r}")
        # parameters compare numerically: `pi/2` and 1.5707963267948966 are one angle,
        # and an exact comparison of floats spelled two ways would be a false alarm
        px, py = x.get("params", []), y.get("params", [])
        if len(px) != len(py):
            diffs.append(f"param count {len(px)} != {len(py)}")
        elif any(abs(float(u) - float(v)) > 1e-9 for u, v in zip(px, py)):
            diffs.append(f"params {px} != {py}")
        if diffs:
            out.append(f"op[{i}]: " + "; ".join(diffs))
        if len(out) >= limit:
            return out
    return out


def cmp_wires(a: dict, b: dict, limit: int) -> list[str]:
    out: list[str] = []
    wa = {w["wire"]: w["ops"] for w in a["wires"]}
    wb = {w["wire"]: w["ops"] for w in b["wires"]}
    for w in sorted(set(wa) | set(wb)):
        x, y = wa.get(w), wb.get(w)
        if x != y:
            # localise to the first position that differs; a whole-sequence dump on a
            # 400-op wire says nothing
            n = min(len(x or []), len(y or []))
            at = next((i for i in range(n) if x[i] != y[i]), n)
            out.append(
                f"wire {w}: diverges at position {at} "
                f"(ours {x[at:at + 4] if x else None}, oracle {y[at:at + 4] if y else None}; "
                f"lengths {len(x or [])} vs {len(y or [])})")
        if len(out) >= limit:
            return out
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ours")
    ap.add_argument("oracle")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    a, b = load(args.ours), load(args.oracle)

    stages = [
        ("shape", [
            f"{k}: ours {a.get(k)}, oracle {b.get(k)}"
            for k in ("n_qubits", "n_clbits")
            if a.get(k) != b.get(k)
        ] + [
            f"{k}: ours {a.get(k)}, oracle {b.get(k)}"
            for k in ("qregs", "cregs")
            if a.get(k) != b.get(k)
        ]),
        ("ops", cmp_ops(a, b, args.limit)),
        ("wires", cmp_wires(a, b, args.limit)),
        ("edges", ([] if [list(e) for e in a["edges"]] == [list(e) for e in b["edges"]]
                   else [f"edge sets differ: ours {len(a['edges'])}, "
                         f"oracle {len(b['edges'])}; "
                         f"only-ours {sorted(map(tuple, a['edges'])) [:3]!r} ..."])),
    ]

    for stage, problems in stages:
        if problems:
            print(f"MISMATCH in {stage}:")
            for p in problems[: args.limit]:
                print(f"  {p}")
            return 1

    if not args.quiet:
        print(f"MATCH: {len(a['ops'])} ops, {len(a['edges'])} edges, "
              f"{len(a['wires'])} wires")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
