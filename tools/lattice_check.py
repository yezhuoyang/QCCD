"""Every distinct merge program of a lattice schedule, checked at the machine's distance
(docs/FULLSTACK_PLAN.md, slice S9): each `Gadget.shape` of kind "merge" written as the corridor
merge it stands for (`qccd.qec.experiments.corridor_merge`, d = 11, 25 rounds), compiled for the
stationary-tile machine (`compile_tiled`), every rule (`qccd.verify`) and R10 (block by block,
the certificate replayed against the source circuit).  One line per program in LOG -- the shape,
its ions, the failed rules, its time, R10 -- and programs already in LOG (or in any OTHER log)
are skipped, so a stopped run resumes.

    python tools/lattice_check.py SHAPES.json LOG [OTHER ...]

SHAPES.json: a list of shapes as `lattice_prime.py` writes them (its `shapes.json`, or a step's
`shapes`); programs other than merges (preparations, readouts, H̄) are checked elsewhere.
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
    return (s[0], s[1], tuple((tuple(t), side) for t, side in s[2]), tuple(tuple(c) for c in s[3]))


def done(logs) -> set:
    got = set()
    for lg in logs:
        if Path(lg).exists():
            got |= {ln.split(" | ")[0] for ln in Path(lg).read_text(encoding="utf-8").splitlines()}
    return got


def check(s) -> str:
    """The log line's body for one merge program at the machine's distance."""
    from qccd.compile.cooling import insert_cooling
    from qccd.compile.tiled_surface import compile_tiled
    from qccd.cost.models import corrected_model
    from qccd.fullstack.scale import machine as M
    from qccd.qec.experiments import corridor_merge
    from qccd.verify import verify
    from test_c2lr_router import _r10_blocks as r10_blocks
    _, letter, pats, cor = s
    e = corridor_merge(M.DISTANCE, letter, list(pats), set(cor), M.MERGE_ROUNDS)
    rows, cols = (max(x[i] for x in e.coords) + 1 for i in (0, 1))
    a = M.device(rows, cols)
    prog, cert, _ = compile_tiled(e.qasm, rows, cols, arch=a, coords=e.coords, faces=e.faces)
    m = corrected_model("quantinuum")
    rep = verify(insert_cooling(prog, a, m).program, a, m, check_metrics=False)
    failed, total = rep.rules.summary().get("failed"), rep.result.total_us
    del rep
    with tempfile.TemporaryDirectory() as tmp:
        blocks = r10_blocks(prog, cert, a, e.qasm, Path(tmp))
    return f"{e.n_data + e.n_ancilla} ions, failed {failed}, {total / 1e3:.1f} ms, R10 ok ({blocks} blocks)"


def parse_log(lines) -> dict:
    """{shape: (ions, failed rules, ms, R10 ok)} from log lines (passing or not)."""
    import ast
    import re
    out = {}
    for ln in lines:
        parts = ln.split(" | ")
        if len(parts) < 3 or not parts[0].startswith("('merge'"):
            continue
        m = re.match(r"(\d+) ions, failed (\[.*?\]), ([\d.]+) ms, (R10 ok)?", parts[2])
        if m:
            out[ast.literal_eval(parts[0])] = (int(m[1]), ast.literal_eval(m[2]), float(m[3]), bool(m[4]))
    return out


def main():
    shapes_file, log, others = sys.argv[1], Path(sys.argv[2]), sys.argv[3:]
    shapes = [shape_key(s) for s in json.loads(Path(shapes_file).read_text(encoding="utf-8")) if s[0] == "merge"]
    shapes.sort(key=lambda s: len(s[3]))              # the short corridors first
    print(f"{len(shapes)} merge programs, {len(done([log, *others]))} logged", flush=True)
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
