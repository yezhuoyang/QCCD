"""Goal 3's sweep: one fixed code, every architecture family, every size the rules allow.

    python goals/goal3/sweep.py --code surface5 [--family Grid Cyclone ...] [--workers 3]
                                [--list] [--max-points N]

Each design point of each family (`families.py`) is graded on the code's release
(`boards.py`: goal3_<code>_mem@1, the Z memory then the X memory on the same ions), the way the
official grader grades a submission to it, minus the certificate chain:

1. the device is the family's generator at that size, entered on the release
   (`board_device`: qccd-physics@2);
2. the reference compile (`compile_with_qccdc`: the pinned qccdc, rotation first on a loop,
   cooling under the "quantinuum" table; on a C2LR device the platform's C2LR router,
   `qccd.compile.c2lr_router.compile_with_router`, at the same table);
3. the rules (`qccd.verify.verify` under corrected_model("quantinuum"), as the rules stage);
4. the metrics (`compute_metrics`: T_quantinuum, instructions, dacs, ...);
5. the logical error rate under qccd-noise@2 with the release's budget and seed
   (`evaluate_memory`, called as the grader's ler stage calls it).

The Lean certificate check (R10's second half) is not run here: it is what the full grader
adds when a point is submitted.  One JSON line per point lands in `results/<code>.jsonl`; the
compile outputs stay in `work/<code>/<point>/`, and a point already in the results file is not
run again.

Set QCCD_QCCDC to the published compiler (the toolchain pin), never a tree build.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

TABLE = "quantinuum"


def release(code: str):
    """The code's Goal 3 release (`boards.py`)."""
    from boards import task_id
    from qccd.workspace.tasks import find_release
    return find_release(f"{task_id(code)}@1")


def load_exp(rel):
    from qccd.qec.experiments import MemoryExperiment
    return MemoryExperiment.from_spec(rel.detectors(), rel.circuit_text())


def rounds_run(exp) -> int:
    """The syndrome rounds the program runs: a Z-then-X memory runs its `rounds` twice."""
    return exp.rounds * (2 if exp.code.endswith("_seq") else 1)


def device_doc(point: dict, rel) -> dict:
    """The family's generator at this size, with the family's control block, entered on the
    release (its physics)."""
    from qccd.api import Machine
    from qccd.workspace.tasks import board_device
    m = getattr(Machine, point["generator"])(**point["params"], name=point["name"])
    doc = m.arch.to_json()
    if point.get("control"):
        doc["control"] = {**doc.get("control", {}), **point["control"]}
    doc["description"] = point.get("about", "")
    doc = board_device(doc, rel)
    if point.get("c2lr"):
        # Machine.c2lr_grid's control and its step's price, on the Goal's physics.  Merge and
        # split stay qccd-physics@2's: arXiv:2403.00756 does not measure them, and every other
        # family pays physics@2's.  The DACs are counted from the paper's census (`wiring`),
        # not one per electrode.
        from qccd.arch.c2lr import c2lr_control, c2lr_primitives
        c = point["c2lr"]
        doc["control"] = c2lr_control(checkerboard=c["checkerboard"], wiring=True)
        own = {k: v for k, v in c2lr_primitives(c["species"]).items() if k not in ("merge", "split")}
        doc["primitives"] = {**doc["primitives"], **own}
    return doc


def grade(code: str, point: dict, work_root: str) -> dict:
    """One point: compile, rules, metrics, LER.  Never raises: a failure is a row."""
    from qccd.compile.c2lr_router import Refused, compile_with_router
    from qccd.qec import CompileFailed, compile_with_qccdc, evaluate_memory
    rel = release(code)
    row = {"code": code, "release": rel.id, "family": point["family"], "point": point["name"],
           "generator": point["generator"], "params": point["params"],
           "control": point.get("control") or {}, **({"c2lr": point["c2lr"]} if point.get("c2lr") else {})}
    t0 = time.time()
    work = Path(work_root) / code / point["name"]
    work.mkdir(parents=True, exist_ok=True)
    try:
        exp = load_exp(rel)
        row["basis"] = exp.basis
        doc = device_doc(point, rel)
        arch_p = work / "device.arch.json"
        arch_p.write_text(json.dumps(doc, indent=1), encoding="utf-8")
        row["ions"] = exp.n_data + exp.n_ancilla
        try:
            if point.get("c2lr"):
                # qccdc's plain shuttles break R4 and C1 on a swap-or-stay device: the platform's
                # C2LR router emits the steps (and checks every rule before it returns)
                cooled, cert_p = compile_with_router(exp.qasm, arch_p, work, table=TABLE)
            else:
                cooled, cert_p = compile_with_qccdc(exp.qasm, arch_p, work, timeout=point.get("timeout", 1800),
                                                    table=TABLE)
        except CompileFailed as exc:
            return {**row, "status": "refused", "why": f"{exc.kind}: {exc}"[:400],
                    "seconds": round(time.time() - t0, 1)}
        except Refused as exc:
            return {**row, "status": "refused", "why": f"c2lr-router: {exc}"[:400],
                    "seconds": round(time.time() - t0, 1)}
        row["compile_seconds"] = round(time.time() - t0, 1)
        row["compiler_mode"] = json.loads((work / "compile.json").read_text(encoding="utf-8")).get("mode")
        from qccd.arch import load
        from qccd.cost.models import corrected_model
        from qccd.ir.tsir import TSIR
        from qccd.verify import verify
        from qccd.workspace.metrics import compute_metrics
        arch = load(str(arch_p))
        prog = TSIR.load(str(cooled))
        rep = verify(prog, arch, corrected_model(TABLE), check_metrics=False)
        s = rep.rules.summary()
        row["rules_failed"] = sorted(s.get("failed") or [])
        if row["rules_failed"]:
            first: dict = {}
            for v in rep.rules.violations:
                first.setdefault(v.rule, str(v)[:240])
            row["why"] = "; ".join(first[r] for r in row["rules_failed"] if r in first)[:400]
        row["rules_skipped"] = sorted(dict(s.get("skipped") or {}))
        values, _ = compute_metrics(arch, prog, rel.manifest["physics"])
        row["metrics"] = {k: v for k, v in values.items() if isinstance(v, (int, float, str, bool)) or v is None}
        T = values.get(f"T_{TABLE}")
        row["T_ms"] = T
        row["T_round_ms"] = (T / rounds_run(exp)) if T is not None else None
        if row["rules_failed"]:
            # a program that breaks a rule is not a design: the figure never plots it, so its
            # LER is not sampled
            return {**row, "status": "rules_failed", "seconds": round(time.time() - t0, 1)}
        q = rel.manifest["qec"]
        budget = dict(q.get("ler") or {})
        cert = json.loads(cert_p.read_text(encoding="utf-8"))
        lr = evaluate_memory(str(cooled), str(arch_p), exp, noise_id=q["noise"], cert=cert,
                             ler_budget={k: v for k, v in budget.items() if k != "seed"},
                             seed=budget.get("seed"))
        (work / "ler_report.json").write_text(json.dumps(lr, indent=1), encoding="utf-8")
        chk = lr.get("check") or {}
        if not chk.get("ok"):
            return {**row, "status": "not_the_experiment", "why": str(chk.get("reason") or chk)[:400],
                    "seconds": round(time.time() - t0, 1)}
        row["ler"] = lr["ler"]
        row["ler_budget"] = budget
        row["budget"] = {k: round(v, 6) for k, v in (lr.get("budget") or {}).items()}
        row["status"] = "ok"
    except Exception as exc:  # noqa: BLE001 -- one point's failure is a row, not the end of the sweep
        row.update(status="error", why=f"{type(exc).__name__}: {exc}"[:400], trace=traceback.format_exc()[-1200:])
    row["seconds"] = round(time.time() - t0, 1)
    return row


def main() -> int:
    from families import FAMILIES, points_for
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True)
    ap.add_argument("--family", nargs="*", default=[])
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--max-points", type=int, default=0, help="per family, smallest first")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--work", default=str(HERE / "work"))
    a = ap.parse_args()
    if not os.environ.get("QCCD_QCCDC"):
        print("set QCCD_QCCDC to the published compiler", file=sys.stderr)
        return 2
    meta = json.loads((HERE / "codes" / f"{a.code}_z" / "meta.json").read_text(encoding="utf-8"))
    pts = []
    for fam in FAMILIES:
        if a.family and fam not in a.family:
            continue
        ps = points_for(fam, meta)
        pts += ps[:a.max_points] if a.max_points else ps
    if a.list:
        for p in pts:
            print(f"{p['family']:9s} {p['name']:24s} {p['generator']}({p['params']})")
        print(f"{len(pts)} points")
        return 0
    out = HERE / "results" / f"{a.code}.jsonl"
    out.parent.mkdir(exist_ok=True)
    done = set()
    if out.exists():
        for ln in out.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                r = json.loads(ln)
                if r.get("status") != "error":
                    done.add(r["point"])
    todo = [p for p in pts if p["name"] not in done]
    print(f"{a.code}: {len(todo)} to run ({len(done)} already done), {a.workers} workers", flush=True)
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(grade, a.code, p, a.work): p["name"] for p in todo}
        for f in as_completed(futs):
            r = f.result()
            with out.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(r, default=str) + "\n")
            ler = (r.get("ler") or {}).get("per_round")
            print(f"{r['family']:9s} {r['point']:24s} {r['status']:13s} "
                  f"T/round={r.get('T_round_ms')} ler/round={ler} rules_failed={r.get('rules_failed')} "
                  f"{r.get('why', '')[:120]} ({r['seconds']} s)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
