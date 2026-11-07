"""Goal 3's sweep: one fixed code, every architecture family, every size the deck allows.

    python goals/goal3/sweep.py --code surface5 [--family C2LR WISE ...] [--workers 3]
                                [--list] [--max-points N] [--cool-us 300]

Each design point of each family (`families.py`) runs the code's experiment from its release
(`boards.py`: the Z memory then the X memory on the same ions), minus the certificate chain:

1. the device is the family's, on the Helios nominal physics (qccd-physics@3) with the transport
   deck's prices where the noise model has none (`device_doc`);
2. the family's compiler, with the cooling pass at the `helios` table: the C2LR, WISE or Cyclone
   router, each keeping the circuit's gate order;
3. the family's own rule set (`families.RULES`), judged as the grader judges an entry's declared set;
4. the metrics (`compute_metrics`: T, instructions, ...), and the deck's hardware census;
5. the logical error rate under qccd-noise@3 with the release's budget and seed (`evaluate_memory`).

The Lean certificate check (R10's second half) is not run here: it is what the full grader adds when
a point is submitted.  One JSON line per point lands in `results/<code>.jsonl`; the compile outputs
stay in `work/<code>/<point>/`, and a point already in the results file is not run again.

`--cool-us` is a sensitivity run: the same sweep with another cooling time, whose rows and compile
outputs go to `results/variants/cool<us>us/` and `work_cool<us>us/`.  The noise model's two cooling
figures, 3 ms and Studio's 300 us, "use different reset assumptions"; the main figure takes its 3 ms.
Cyclone's cooling takes no time (the deck's S10), so the option does not change a Cyclone point.
"""

from __future__ import annotations

import argparse
import json
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

TABLE = "helios"
NOISE = "qccd-noise@3"
PHYSICS = ROOT / "qccd" / "workspace" / "physics" / "qccd-physics@3.json"


def release(code: str):
    """The code's current Goal 3 release (`boards.py`): its circuit, detectors and LER budget."""
    from boards import release_id
    from qccd.workspace.tasks import find_release
    return find_release(release_id(code))


def load_exp(rel):
    from qccd.qec.experiments import MemoryExperiment
    return MemoryExperiment.from_spec(rel.detectors(), rel.circuit_text())


def rounds_run(exp) -> int:
    """The syndrome rounds the program runs: a Z-then-X memory runs its `rounds` twice."""
    return exp.rounds * (2 if exp.code.endswith("_seq") else 1)


def physics() -> dict:
    return json.loads(PHYSICS.read_text(encoding="utf-8"))


def device_doc(point: dict, exp) -> dict:
    """The family's device at this size, on the Helios nominal physics with the deck's prices."""
    from qccd.api import Machine
    from qccd.arch import c2lr, cyclone, wise
    phys = physics()
    p = point["params"]
    heating = phys["heating"]
    if point["generator"] == "grid":
        doc = Machine.grid(p["a"], p["b"], name=point["name"]).arch.to_json()
        doc["control"] = c2lr.c2lr_control(checkerboard=False, wiring=True)
        prims = {**phys["primitives"], **{k: dict(v) for k, v in c2lr.C2LR_DECK[point["species"]].items()}}
        species = {**phys["species"], "pair": point["species"], "pair_source": "arXiv:2403.00756 v2"}
    elif point["generator"] == "wise":
        doc = wise.device_doc(p["h"], p["cx"], p["cy"], name=point["name"])
        prims, species = wise.primitives(phys["primitives"]), phys["species"]
    elif point["generator"] == "cyclone":
        doc = cyclone.device_doc(p["x"], p["n"], name=point["name"])
        prims, species = cyclone.primitives(phys["primitives"], p["n"]), phys["species"]
        heating = {**phys["heating"], "anomalous_rate_quanta_per_ms": 0.0,
                   "note": "sympathetic cooling is assumed after every gate, so heat is not tracked",
                   "source": "QCCD Transport Architectures deck, Cyclone S10"}
    else:
        raise KeyError(point["generator"])
    if point.get("cool_us") is not None and point["generator"] != "cyclone":
        prims = {**prims, "cool": {**prims["cool"], "us": point["cool_us"]}}
    doc.update(primitives=prims, heating=heating, species=species, budget=phys["budget"])
    doc["description"] = point.get("about", "")
    return doc


def census(point: dict, exp) -> dict:
    """The deck's hardware table for this point."""
    from qccd.arch import c2lr, cyclone, wise
    p = point["params"]
    if point["generator"] == "grid":
        return {**c2lr.deck_census(p["a"], p["b"]), "legs_drawn": 2 * p["a"] * p["b"] - p["a"] - p["b"]}
    if point["generator"] == "wise":
        return wise.census(p["h"], p["cx"], p["cy"])
    return cyclone.census(exp.n_data, exp.n_ancilla, p["x"], p["n"], ancillas=exp.n_ancilla)


def compile_point(point: dict, exp, arch_p: Path, work: Path) -> tuple[Path, Path]:
    from qccd.compile import c2lr_router, cyclone_router, wise_router
    if point["generator"] == "grid":
        return c2lr_router.compile_with_router(exp.qasm, arch_p, work, table=TABLE, reads_together=True)
    if point["generator"] == "wise":
        return wise_router.compile_with_router(exp.qasm, arch_p, work, table=TABLE)
    return cyclone_router.compile_with_router(exp.qasm, arch_p, work, n_data=exp.n_data, table=TABLE)


def grade(code: str, point: dict, work_root: str) -> dict:
    """One point: compile, rules, metrics, LER.  Never raises: a failure is a row."""
    from families import COMPILERS, RULES
    from qccd.compile.c2lr_router import Refused
    from qccd.qec import evaluate_memory
    rel = release(code)
    row = {"code": code, "release": rel.id, "noise": NOISE, "physics": "qccd-physics@3", "family": point["family"],
           "point": point["name"], "generator": point["generator"], "params": point["params"],
           "deck": point.get("deck") or {}, "compiler": COMPILERS[point["family"]],
           "rule_set": RULES[point["family"]],
           **({"cool_us": point["cool_us"]} if point.get("cool_us") is not None else {})}
    t0 = time.time()
    work = Path(work_root) / code / point["name"]
    work.mkdir(parents=True, exist_ok=True)
    try:
        exp = load_exp(rel)
        row["basis"] = exp.basis
        row["census"] = census(point, exp)
        arch_p = work / "device.arch.json"
        arch_p.write_text(json.dumps(device_doc(point, exp), indent=1), encoding="utf-8")
        row["ions"] = exp.n_data + exp.n_ancilla
        try:
            cooled, cert_p = compile_point(point, exp, arch_p, work)
        except Refused as exc:
            return {**row, "status": "refused", "why": str(exc)[:400], "seconds": round(time.time() - t0, 1)}
        row["compile_seconds"] = round(time.time() - t0, 1)
        row["compiler_mode"] = json.loads((work / "compile.json").read_text(encoding="utf-8")).get("mode")
        from qccd.arch import load
        from qccd.cost.models import corrected_model
        from qccd.ir.tsir import TSIR
        from qccd.ruleset import FAILS, HOLDS, check, legacy_config
        from qccd.ruleset import load as load_rules
        from qccd.ruleset.compile import circuit_of_certificate
        from qccd.ruleset.programme import programme_execution
        from qccd.workspace.metrics import compute_metrics
        arch = load(str(arch_p))
        prog = TSIR.load(str(cooled))
        cert = json.loads(cert_p.read_text(encoding="utf-8"))
        rs = load_rules(RULES[point["family"]])
        exe = programme_execution(prog, arch, corrected_model(TABLE), circuit=circuit_of_certificate(cert),
                                  rule_config=legacy_config(rs) or None, check_metrics=False)
        rep = check(rs, exe)
        T = max(exe.instant_time) / 1000.0
        row["rules_failed"] = sorted(x.id for x in rep.results if x.verdict == FAILS)
        row["rules_skipped"] = sorted(x.id for x in rep.results if x.verdict not in (HOLDS, FAILS))
        if row["rules_failed"]:
            row["why"] = "; ".join(f"{x.id}: {x.count} times" for x in rep.results if x.verdict == FAILS)[:400]
        values, _ = compute_metrics(arch, prog, {"rank_table": TABLE, "tables": [TABLE]})
        row["metrics"] = {k: v for k, v in values.items() if isinstance(v, (int, float, str, bool)) or v is None}
        del exe, rep                    # gigabytes on a long programme, and sampling takes minutes
        row["T_ms"] = T
        row["T_round_ms"] = T / rounds_run(exp)
        row["cools"] = sum(i.type == "cool" for i in prog.instructions)
        row["instructions"] = len(prog.instructions)
        row["transport_steps"] = sum(i.type == "simd" for i in prog.instructions)
        if row["rules_failed"]:
            # a program that breaks a rule is not a design: the figure never plots it, so its
            # LER is not sampled
            return {**row, "status": "rules_failed", "seconds": round(time.time() - t0, 1)}
        budget = dict((rel.manifest["qec"].get("ler")) or {})
        lr = evaluate_memory(str(cooled), str(arch_p), exp, noise_id=NOISE, cert=cert,
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
    ap.add_argument("--work", default=None)
    ap.add_argument("--cool-us", type=float, default=None, help="a sensitivity run at this cooling time")
    a = ap.parse_args()
    tag = f"cool{a.cool_us:g}us" if a.cool_us is not None else None
    a.work = a.work or str(HERE / (f"work_{tag}" if tag else "work"))
    meta = json.loads((HERE / "codes" / f"{a.code}_z" / "meta.json").read_text(encoding="utf-8"))
    pts = []
    for fam in FAMILIES:
        if a.family and fam not in a.family:
            continue
        ps = points_for(fam, meta)
        pts += ps[:a.max_points] if a.max_points else ps
    if tag:
        pts = [{**p, "cool_us": a.cool_us} for p in pts if p["generator"] != "cyclone"]
    if a.list:
        for p in pts:
            print(f"{p['family']:9s} {p['name']:24s} {p['generator']}({p['params']})")
        print(f"{len(pts)} points")
        return 0
    out = HERE / "results" / ("variants/" + tag if tag else "") / f"{a.code}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        for ln in out.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                r = json.loads(ln)
                if r.get("status") != "error":
                    done.add(r["point"])
    todo = [p for p in pts if p["name"] not in done]
    print(f"{a.code}: {len(todo)} to run ({len(done)} already done), {a.workers} workers", flush=True)
    # a process per point: a long programme's run takes gigabytes, and a worker that goes on to the next keeps them
    one_each = {"max_tasks_per_child": 1} if sys.version_info >= (3, 11) else {}
    with ProcessPoolExecutor(max_workers=a.workers, **one_each) as ex:
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
