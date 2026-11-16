"""Every program a lattice run copies, as the machine runs it (docs/FULLSTACK_PLAN.md, slice S9):
for each distinct shape in the runs given (their steps' `shapes`), and the idle round a held patch
runs, the program built at the machine's distance (`qccd.fullstack.scale.programs`), cooled and
replayed -- its exact machine time, and every ion's moves and pulses in one file for a viewer
(`qccd.gadget.rsa.ion_bytes`).  In parallel; a stopped run resumes.

    python tools/lattice_programs.py OUT RUN [RUN ...] [--workers N]
    python tools/lattice_programs.py OUT --pages [--workers N]

OUT/programs.jsonl: one line a program -- {key, shape, us, ions, instructions, ion_file}; OUT/ions/
<key>.bin.gz.  `--pages` then makes OUT/pages/<key>.html.gz for every program made that has none:
the program on the studio's stage, its design page (every rule replayed for its panels).  `key`
is the first 12 hex digits of the shape's JSON's SHA-1.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def key_of(shape) -> str:
    return hashlib.sha1(json.dumps(shape).encode()).hexdigest()[:12]


def as_tuple(s) -> tuple:
    """A shape as JSON gives it, as `lattice` makes it."""
    if s[0] == "merge":
        return (s[0], s[1], tuple((tuple(t), side) for t, side in s[2]), tuple(tuple(c) for c in s[3]))
    return tuple(s)


def shapes_of(runs) -> list:
    got = {}
    for run in runs:
        for p in sorted((Path(run) / "steps").glob("*.json")):
            for s, *_ in json.loads(p.read_text(encoding="utf-8")).get("shapes", []):
                got.setdefault(json.dumps(s), s)
    return [["idle"]] + sorted(got.values(), key=lambda s: (s[0] != "merge", json.dumps(s)))


def one(shape, out: str) -> dict:
    from qccd.fullstack.scale import programs as P
    from qccd.gadget.rsa import ion_bytes
    from qccd.verify.replay import replay
    t = time.time()
    out = Path(out)
    b = P.build(as_tuple(shape))
    prog = P.cooled(b)
    res = replay(prog, b.arch, P._model(), check_rules=False)
    k = key_of(shape)
    (out / "ions" / f"{k}.bin.gz").write_bytes(gzip.compress(ion_bytes(b.arch, prog, res.cycles), 9, mtime=0))
    return {"key": k, "shape": shape, "us": res.total_us, "ions": b.ions, "instructions": len(prog.instructions),
            "ion_file": (out / "ions" / f"{k}.bin.gz").stat().st_size, "s": round(time.time() - t, 1)}


def page(shape, out: str) -> dict:
    """The program's design page: on the studio's stage, every rule replayed for its panels, the
    hardware program beside the circuit it was compiled from (Hardware / Gates / Both)."""
    import tempfile

    from qccd.fullstack.scale import programs as P
    from qccd.gadget.rsa import title
    from qccd.ir.source_map import build as build_source
    from qccd.verify import verify
    from qccd.viz import render_html
    t = time.time()
    b = P.build(as_tuple(shape))
    prog = P.cooled(b)
    rep = verify(prog, b.arch, P._model(), check_metrics=False)
    with tempfile.TemporaryDirectory() as tmp:
        qasm = Path(tmp) / f"{key_of(shape)}.qasm"
        qasm.write_text(b.qasm, encoding="utf-8")
        source = build_source(prog, b.cert, qasm)
    f = Path(out) / "pages" / f"{key_of(shape)}.html"
    render_html(b.arch, prog, rep.result, P._model(), f, kicker="RSA-2048 at d = 11", headline=title(shape),
                lede=f"{b.ions:,} ions, {rep.result.total_us / 1000:,.3f} ms: one of the programs a prime copies",
                source=source, template_stems="*")
    raw = f.stat().st_size
    f.with_name(f.name + ".gz").write_bytes(gzip.compress(f.read_bytes(), 9, mtime=0))
    f.unlink()
    return {"key": key_of(shape), "raw": raw, "gz": f.with_name(f.name + ".gz").stat().st_size, "s": round(time.time() - t, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("runs", nargs="*")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--pages", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    (out / "ions").mkdir(parents=True, exist_ok=True)
    (out / "pages").mkdir(exist_ok=True)
    log = out / "programs.jsonl"
    rows = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
    if a.pages:
        todo = [r["shape"] for r in sorted(rows, key=lambda r: r["ions"])
                if not (out / "pages" / f"{r['key']}.html.gz").exists()]
        print(f"{len(todo)} pages to make", flush=True)
        with ProcessPoolExecutor(a.workers) as ex:
            futs = {ex.submit(page, s, str(out)): s for s in todo}
            for f in as_completed(futs):
                try:
                    print(json.dumps(f.result()), flush=True)
                except Exception as e:                 # noqa: BLE001 -- reported, the rest go on
                    print("FAILED", futs[f], repr(e)[:300], flush=True)
        return
    done = {r["key"] for r in rows}
    todo = [s for s in shapes_of(a.runs) if key_of(s) not in done]
    print(f"{len(todo)} programs to make, {len(done)} made", flush=True)
    # the big corridors first, so the pool ends on small ones
    todo.sort(key=lambda s: -(len(s[3]) + len(s[2]) if s[0] == "merge" else 0))
    with ProcessPoolExecutor(a.workers) as ex:
        futs = {ex.submit(one, s, str(out)): s for s in todo}
        for f in as_completed(futs):
            try:
                row = f.result()
            except Exception as e:                     # noqa: BLE001 -- reported, the rest go on
                print("FAILED", futs[f], repr(e)[:300], flush=True)
                continue
            with log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
            print(row["key"], row["shape"][0], row["ions"], row["us"], row["ion_file"], row["s"], flush=True)


if __name__ == "__main__":
    main()
