"""Does today's pinned compiler still write the sweep's Grid and WISE programs?

    QCCD_QCCDC=<pinned qccdc> python goals/goal3/same_compiler.py --work <work dir> [<work dir> ...]
                                                                 --code surface3 [...] [--workers 2]

The Grid and WISE points were compiled by qccdc b4a003dbb4ae, the pin when they were swept; the
Cyclone and Conveyor points, swept again with junction corners, by a later one.  Each compiled
Grid or WISE point is compiled again here, on its own device file from the sweep's
`work/<code>/<point>/`, with the compiler QCCD_QCCDC names and the release's limit, and the two
cooled programs are compared instruction by instruction, each without its `meta` (provenance:
the tree's path, and the source-op fields newer trees add).  The answers are merged into
`results/compiler_identity.json`, one row per point.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (ROOT, HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

OUT = HERE / "results" / "compiler_identity.json"


def body_hash(p: Path) -> str:
    doc = json.loads(p.read_text(encoding="utf-8"))
    ins = [{k: v for k, v in i.items() if k != "meta"} for i in doc["instructions"]]
    return hashlib.sha256(json.dumps(ins, sort_keys=True).encode()).hexdigest()


def one(code: str, row: dict, work: str) -> dict:
    import sweep
    from qccd.qec import CompileFailed, compile_with_qccdc
    rel = sweep.release(code)
    exp = sweep.load_exp(rel)
    old = Path(work)
    t0 = time.time()
    out = {"code": code, "point": row["point"], "old_compiler": row["compiler"], "new_compiler": sweep.compiler_name()}
    with tempfile.TemporaryDirectory() as tmp:
        arch_p = Path(tmp) / "device.arch.json"
        arch_p.write_bytes((old / "device.arch.json").read_bytes())
        limit = (rel.manifest.get("limits") or {}).get("compile_timeout_s", 1800)
        try:
            cooled, _ = compile_with_qccdc(exp.qasm, arch_p, Path(tmp), timeout=limit, table="quantinuum")
        except CompileFailed as exc:
            return {**out, "same": False, "why": f"refused: {exc}"[:300]}
        a, b = body_hash(old / "prog.cooled.tsir.json"), body_hash(Path(cooled))
    return {**out, "same": a == b, "old": a[:16], "new": b[:16], "seconds": round(time.time() - t0, 1)}


def main() -> int:
    from pages import find_work, rows
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", type=Path, nargs="+", required=True)
    ap.add_argument("--code", nargs="+", required=True)
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()
    todo = []
    for code in a.code:
        for r in rows(code):
            if r.get("c2lr") or r["generator"] != "grid" or r["status"] not in ("ok", "rules_failed"):
                continue
            w = find_work(code, r["point"], a.work)
            if w is None:
                print(f"{code} {r['point']}: no work folder", flush=True)
                continue
            todo.append((code, r, str(w)))
    have = {(r["code"], r["point"]): r for r in json.loads(OUT.read_text(encoding="utf-8"))} if OUT.exists() else {}
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for f in as_completed([ex.submit(one, *t) for t in todo]):
            r = f.result()
            have[(r["code"], r["point"])] = r
            print(json.dumps(r), flush=True)
            OUT.write_text(json.dumps(sorted(have.values(), key=lambda r: (r["code"], r["point"])), indent=1) + "\n",
                           encoding="utf-8")
    res = [have[k] for k in have if k[0] in a.code]
    print(f"{sum(r['same'] for r in res)} of {len(res)} identical", flush=True)
    return 0 if all(r["same"] for r in res) else 1


if __name__ == "__main__":
    raise SystemExit(main())
