"""Goal 3 against the shuttling speed: each family's best point, at a range of speeds.

    python goals/goal3/speed.py --code surface5 [--speeds 1 2 4 6 10 20 35] [--family ...] [--workers 2]

The main figure prices a straight rail at a fixed time whatever its length (the Helios nominal
model's 5 us, the deck's 10 us Cyclone move, half of WISE's t0), which on the 464 um trap pitch
the pages are drawn in is 93, 46 and 9 m/s.  Here a rail takes its length over one shuttling
speed, the same for every family, and heats as the speed's square (`qccd.arch.speed`): slower is
longer idle time on every waiting ion, faster is more heat for the next gate or one more cooling.
Junction crossings, splits, merges, a C2LR swap-or-stay step and a WISE junction step keep their
own sources' times, so a family that moves only by those (C2LR) does not change.

For each family of a code this takes the point with the lowest logical error rate in
`results/<code>.jsonl` and grades it at each speed exactly as `sweep.py` does: the family's
compiler (a point compiled by qccdc is routed once and cooled again at each speed, since qccdc's
routing does not read the prices), the rules, the time, the logical error rate.  One JSON line per
(point, speed) lands in `results/speed/<code>.jsonl`; outputs stay in `work_speed/<v>/`.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

SPEEDS = (1.0, 2.0, 4.0, 6.0, 10.0, 20.0, 35.0)


def best_points(code: str, families: list[str]) -> list[dict]:
    """Each family's point with the lowest logical error rate (the faster on a tie), as a sweep point."""
    from families import FAMILIES, points_for
    meta = json.loads((HERE / "codes" / f"{code}_z" / "meta.json").read_text(encoding="utf-8"))
    rows: dict = {}
    f = HERE / "results" / f"{code}.jsonl"
    for ln in f.read_text(encoding="utf-8").splitlines() if f.exists() else []:
        if ln.strip():
            r = json.loads(ln)
            rows[(r["family"], r["point"])] = r
    out = []
    for fam in FAMILIES:
        if families and fam not in families:
            continue
        ok = [r for (ff, _), r in rows.items() if ff == fam and r.get("status") == "ok"]
        if not ok:
            continue
        b = min(ok, key=lambda r: (r["ler"]["per_round"], r["T_round_ms"]))
        point = next((p for p in points_for(fam, meta) if p["name"] == b["point"]), None)
        if point is not None:
            out.append({**point, "ler_batch": (b.get("ler_budget") or {}).get("batch")})
    return out


def _recool(base: Path, arch_p: Path, work: Path) -> tuple[Path, Path]:
    """qccdc's routed program from the main sweep's folder, cooled again on this machine's prices."""
    from qccd.arch import load
    from qccd.compile.cooling import CoolingPolicy, insert_cooling
    from qccd.cost.models import corrected_model
    from qccd.ir.tsir import TSIR
    import sweep
    for name in ("circuit.qasm", "prog.tsir.json", "prog.qcert.json", "compile.json", "device.expanded.json"):
        if (base / name).exists():
            shutil.copy2(base / name, work / name)
    prog = TSIR.load(str(work / "prog.tsir.json"))
    cooled = insert_cooling(prog, load(str(arch_p)), corrected_model(sweep.TABLE), policy=CoolingPolicy(lazy=True)).program
    cooled.meta.update(prog.meta)
    final = work / "prog.cooled.tsir.json"
    cooled.save(str(final), indent=None)
    return final, work / "prog.qcert.json"


def grade(code: str, point: dict, work_root: str, base_root: str) -> dict:
    import sweep
    base = Path(base_root) / code / point["name"]
    if point["generator"] in ("lattice", "ring") and (base / "prog.tsir.json").exists():
        compile_point = sweep.compile_point
        sweep.compile_point = lambda p, exp, arch_p, work: _recool(base, arch_p, work)
        try:
            return sweep.grade(code, point, work_root)
        finally:
            sweep.compile_point = compile_point
    return sweep.grade(code, point, work_root)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True)
    ap.add_argument("--speeds", nargs="*", type=float, default=list(SPEEDS))
    ap.add_argument("--family", nargs="*", default=[])
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--base", default=str(HERE / "work"), help="the main sweep's folders (qccdc's routed programs)")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    pts = best_points(a.code, a.family)
    out = HERE / "results" / "speed" / f"{a.code}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        for ln in out.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                r = json.loads(ln)
                if r.get("status") != "error":
                    done.add((r["point"], float(r["speed_m_s"])))
    todo = [({**{k: v for k, v in p.items() if not (k == "ler_batch" and not v)}, "speed_m_s": v}, v)
            for p in pts for v in a.speeds if (p["name"], float(v)) not in done]
    if a.list:
        for p, v in todo:
            print(f"{p['family']:9s} {p['name']:24s} {v:g} m/s")
        print(f"{len(todo)} to run ({len(done)} done)")
        return 0
    print(f"{a.code}: {len(todo)} to run ({len(done)} done), {a.workers} workers", flush=True)
    one_each = {"max_tasks_per_child": 1} if sys.version_info >= (3, 11) else {}
    with ProcessPoolExecutor(max_workers=a.workers, **one_each) as ex:
        futs = {ex.submit(grade, a.code, p, str(HERE / "work_speed" / f"{v:g}"), a.base): (p["name"], v) for p, v in todo}
        for f in as_completed(futs):
            r = f.result()
            with out.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(r, default=str) + "\n")
            ler = (r.get("ler") or {}).get("per_round")
            print(f"{r['family']:9s} {r['point']:24s} {r.get('speed_m_s')} m/s {r['status']:13s} "
                  f"T/round={r.get('T_round_ms')} ler/round={ler} {r.get('why', '')[:100]} ({r['seconds']} s)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
