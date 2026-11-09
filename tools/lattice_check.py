"""Every distinct program of a lattice schedule, checked at the machine's distance
(docs/FULLSTACK_PLAN.md, slice S9): each shape a step's gadgets copy -- a corridor merge, H̄, a
preparation, a readout, or the idle round a held patch runs -- built by
`qccd.fullstack.scale.programs` (d = 11, 25 rounds a merge), compiled for the stationary-tile
machine, every rule (`qccd.verify`) and R10 (block by block, the certificate replayed against the
source circuit).  One line per program in LOG -- the shape, its ions, the failed rules, its time
to the microsecond, R10 -- and programs already in LOG (or in any OTHER log) are skipped, so a
stopped run resumes.

    python tools/lattice_check.py SHAPES.json LOG [OTHER ...]

SHAPES.json: a list of shapes as `lattice_prime.py` writes them (its `shapes.json`, or a step's
`shapes`).
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))


def shape_key(s) -> tuple:
    """A shape as JSON gives it, as the tuple `lattice` makes."""
    if s[0] != "merge":
        return tuple(s)
    return (s[0], s[1], tuple((tuple(t), side) for t, side in s[2]), tuple(tuple(c) for c in s[3]))


def done(logs) -> set:
    got = set()
    for lg in logs:
        if Path(lg).exists():
            got |= {ln.split(" | ")[0] for ln in Path(lg).read_text(encoding="utf-8").splitlines()}
    return got


def check(s) -> str:
    """The log line's body for one program at the machine's distance."""
    from qccd.fullstack.scale import programs as P
    with tempfile.TemporaryDirectory() as tmp:
        r = P.check(P.build(s), tmp)
    return (f"{r['ions']} ions, failed {r['failed']}, {r['us'] / 1e3:.3f} ms, "
            f"R10 ok ({r['r10_blocks']} blocks)")


def parse_log(lines) -> dict:
    """{shape: (ions, failed rules, ms, R10 ok)} from log lines (passing or not)."""
    import ast
    import re
    out = {}
    for ln in lines:
        parts = ln.split(" | ")
        if len(parts) < 3 or not parts[0].startswith("('"):
            continue
        m = re.match(r"(\d+) ions, failed (\[.*?\]), ([\d.]+) ms, (R10 ok)?", parts[2])
        if m:
            out[ast.literal_eval(parts[0])] = (int(m[1]), ast.literal_eval(m[2]), float(m[3]), bool(m[4]))
    return out


def main():
    shapes_file, log, others = sys.argv[1], Path(sys.argv[2]), sys.argv[3:]
    shapes = [shape_key(s) for s in json.loads(Path(shapes_file).read_text(encoding="utf-8"))]
    shapes.sort(key=lambda s: len(s[3]) if s[0] == "merge" else 0)     # the short corridors first
    print(f"{len(shapes)} programs, {len(done([log, *others]))} logged", flush=True)
    for k, s in enumerate(shapes):
        if repr(s) in done([log, *others]):
            continue
        t = time.time()
        line = f"{s!r} | x1 | {check(s)} | {time.time() - t:.0f} s"
        with log.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        print(k, line, flush=True)


if __name__ == "__main__":
    main()
