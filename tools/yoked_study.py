"""Yoked cold storage at our noise (docs/FULLSTACK_PLAN.md, slice S10 step 1): what inner distance a
2D yoked surface code needs so that a stored logical qubit fails no more often a round than a hot
d = 11 patch (3.3e-16, `qccd.fullstack.scale.machine`).

The inner patch is the distance study's (`tools/distance_study.py`): a stationary d x d patch compiled
by `qccd.compile.tiled_surface` on the physics@2 clock, under qccd-noise@2 with the best published
fidelities ("best"), every probability times s.  Here it runs 10d rounds, the length Gidney, Newman,
Brooks and Jones sample gaps over, with its observable also a detector (`yoked.with_gap_detector`);
each shot gives a signed gap (`yoked.signed_gaps`).  `qccd.fullstack.scale.yoked.outer` turns a
10d-round histogram into the yoke's failure per outer round.  At s = 1/2, the machine's noise, small
gaps are too rare to sample, so the yoke is measured at larger s and fitted, as the distance study
fits the hot patch.

    python tools/yoked_study.py build             circuits -> out/yoked_study/*.stim
    python tools/yoked_study.py sample SHOTS [N] [GLOB]  gap histograms (resumable: gaps/<name>.json),
                                                  N workers, the circuits named GLOB (all)
    python tools/yoked_study.py outer [W ...]     every histogram through the outer code
    python tools/yoked_study.py paper GAPS POINTS the estimator on Gidney et al.'s own data: their 10d-round
                                                  gap histograms (stats_gap_distribution.csv) against their
                                                  gap-sampled 2D points (stats_extrapolation.csv), both in
                                                  code.zip/assets of zenodo.org/records/10277397 (CC BY 4.0)
"""

from __future__ import annotations

import json
import zlib
import sys
import time
from dataclasses import replace
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "out" / "yoked_study"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

RUNS = [  # (s, distances): each where a 10d-round shot fails about 1e-5 .. 1e-3 of the time
    (0.5, (3, 5)),
    (1.0, (3, 5)),
    (2.0, (3, 5, 7)),
    (4.0, (3, 5, 7)),
    (8.0, (5, 7)),
]


def build() -> None:
    import stim
    from distance_study import scenario_arch
    from qccd.compile.cooling import insert_cooling
    from qccd.compile.tiled_surface import compile_tiled, device
    from qccd.cost.models import corrected_model
    from qccd.fullstack.scale.yoked import with_gap_detector
    from qccd.qec.experiments import rotated_surface
    from qccd.qec.extract import extract
    from qccd.qec.noise import NoiseModel2

    OUT.mkdir(parents=True, exist_ok=True)
    model = corrected_model("quantinuum")
    compiled = {}
    for s, ds in RUNS:
        for d in ds:
            for basis in "XZ":
                name = f"best_s{s:g}_d{d}_r{10 * d}_{basis}"
                if (OUT / f"{name}.stim").exists():
                    continue
                if (d, basis) not in compiled:
                    t0 = time.time()
                    exp = rotated_surface(d, 10 * d, basis)
                    prog, cert, arch = compile_tiled(exp.qasm, d, arch=device(d, physics="physics@2"))
                    cooled = insert_cooling(prog, arch, model).program
                    cooled.meta.update(prog.meta)
                    compiled[d, basis] = (exp, cooled, cert, arch.to_json())
                    print(f"compiled d={d} {basis}, {10 * d} rounds ({time.time() - t0:.0f} s)", flush=True)
                exp, cooled, cert, doc = compiled[d, basis]
                ex = extract(cooled, scenario_arch(doc, "best"), exp, noise=replace(NoiseModel2(), scale=s),
                             cert=cert)
                c = with_gap_detector(ex.circuit)
                c.detector_error_model(decompose_errors=True)          # matchable, gap detector and all
                (OUT / f"{name}.stim").write_text(str(c), encoding="utf-8")
                print(json.dumps({"name": name, "s": s, "d": d, "basis": basis,
                                  "detectors": c.num_detectors}), flush=True)


def _one(job):
    path, shots, seed = job
    import stim
    from qccd.fullstack.scale.yoked import signed_gaps
    c = stim.Circuit(Path(path).read_text(encoding="utf-8"))
    t0 = time.time()
    h = signed_gaps(c, shots, seed=seed)
    return path, shots, h, time.time() - t0


def sample(shots: int, workers: int = 1, chunk: int = 2_000_000, only: str = "*") -> None:
    """Every circuit whose name matches `only` up to `shots` shots, in chunks; each chunk adds to
    gaps/<name>.json.  A chunk's seed is its circuit's name and its first shot, so no two chunks
    of a circuit share one, whatever the chunk size."""
    (OUT / "gaps").mkdir(parents=True, exist_ok=True)
    jobs = []
    for p in sorted(OUT.glob(f"{only}.stim")):
        g = OUT / "gaps" / f"{p.stem}.json"
        have = json.loads(g.read_text(encoding="utf-8"))["shots"] if g.exists() else 0
        while have < shots:
            n = min(chunk, shots - have)
            jobs.append((str(p), n, zlib.crc32(f"{p.stem}:{have}".encode())))
            have += n
    print(f"{len(jobs)} chunks", flush=True)

    def add(res):
        path, n, h, dt = res
        name = Path(path).stem
        g = OUT / "gaps" / f"{name}.json"
        cur = json.loads(g.read_text(encoding="utf-8")) if g.exists() else {"shots": 0, "seconds": 0, "hist": {}}
        cur["shots"] += n
        cur["seconds"] = round(cur["seconds"] + dt, 1)
        for k, v in h.items():
            cur["hist"][str(k)] = cur["hist"].get(str(k), 0) + v
        g.write_text(json.dumps(cur), encoding="utf-8")
        wrong = sum(v for k, v in cur["hist"].items() if int(k) < 0)
        print(f"{name}: {cur['shots']:,} shots, {wrong} wrong ({dt:.0f} s)", flush=True)

    if workers <= 1:
        for j in jobs:
            add(_one(j))
        return
    from multiprocessing import Pool
    with Pool(workers) as pool:
        for res in pool.imap_unordered(_one, jobs):
            add(res)


def outer(ws=(16,)) -> None:
    """Each (s, d) with both bases sampled, through `yoked.rectangle_terms` (the estimator checked
    against the paper's own points by `paper`): the yoke's failure per outer round, X and Z added,
    and per stored logical qubit per round, beside a hot patch of the same d at the same s (the
    distance study's fit, p = 0.025 (s / 103)^((d + 1) / 2))."""
    from qccd.fullstack.scale.yoked import GapTable, outer_period, rectangle_terms
    got = {}
    for g in sorted((OUT / "gaps").glob("*.json")):
        cur = json.loads(g.read_text(encoding="utf-8"))
        _, s, d, _, basis = g.stem.split("_")
        got[float(s[1:]), int(d[1:]), basis] = cur
    rows = []
    for (s, d) in sorted({(s, d) for s, d, _ in got}):
        if not all((s, d, b) in got for b in "XZ"):
            continue
        for w in ws:
            ri, k = outer_period(d, w), w * w - 4 * w + 2
            row = {"s": s, "d": d, "w": w, "ri": ri, "k": k}
            total = 0.0
            for b in "XZ":
                cur = got[s, d, b]
                tab = GapTable.from_signed({int(x): v for x, v in cur["hist"].items()})
                rc = rectangle_terms(tab.over(ri / (10 * d)), w)
                row[b] = {"shots": cur["shots"], "wrong": sum(v for x, v in cur["hist"].items() if int(x) < 0),
                          "q10": tab.q, "pair": rc["pair"], "one": rc["one"], "three": rc["three"]}
                total += rc["total"]
            row["outer_round"] = total
            row["per_logical_round"] = total / (k * ri)
            row["hot_same_d"] = 0.025 * (s / 103) ** ((d + 1) / 2)
            rows.append(row)
            print(f"s={s:<4g} d={d} W={w}: {total:.3g} a outer round ({ri} rounds), {row['per_logical_round']:.3g} "
                  f"per logical qubit per round (hot d={d}: {row['hot_same_d']:.3g}); shots "
                  f"{row['X']['shots']:,}/{row['Z']['shots']:,}, wrong {row['X']['wrong']}/{row['Z']['wrong']}", flush=True)
    (OUT / "outer.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")


def paper(gaps_csv: str, points_csv: str) -> None:
    """`yoked.rectangle_terms` on the paper's gap histograms at each of its gap-sampled 2D points
    (W = sqrt(cbs), sbs x d rounds between checks, 10 outer rounds a shot, "magic" memories: both
    bases), against the failures it counted.  Writes out/yoked_study/paper_check.json."""
    import csv
    import re
    from qccd.fullstack.scale.yoked import GapTable, rectangle_terms
    csv.field_size_limit(1 << 30)
    tabs = {}
    with open(gaps_csv, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f, skipinitialspace=True):
            m = json.loads(r["json_metadata"])
            if m.get("collect") == "gap" and m.get("patches") == 1 and m["r"] == 10 * m["d"]:
                h = {}
                for k, v in json.loads(r["custom_counts"]).items():
                    h[int(k[1:])] = h.get(int(k[1:]), 0) + v
                tabs[m["d"]] = GapTable.from_signed(h)
    rows = []
    with open(points_csv, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f, skipinitialspace=True):
            mt = re.search(r"_d(\d+)_r(\d+)_.*_dim2_cbs(\d+)_sbs([\d.]+)_tbs", r["strong_id"])
            if not mt or int(mt[1]) not in tabs:
                continue
            d, ro, w, sbs = int(mt[1]), int(mt[2]), int(round(int(mt[3]) ** 0.5)), float(mt[4])
            ours = 2 * rectangle_terms(tabs[d].over(sbs / 10), w)["total"]
            theirs = int(r["errors"]) / int(r["shots"]) / ro
            rows.append({"w": w, "d": d, "ri_over_d": sbs, "errors": int(r["errors"]), "theirs": theirs,
                         "ours": ours, "ratio": ours / theirs if theirs else None})
    rows.sort(key=lambda r: (r["w"], r["d"], r["ri_over_d"]))
    for r in rows:
        print(f"W={r['w']:2d} d={r['d']:2d} ri={r['ri_over_d']:3.0f}d: theirs {r['theirs']:9.3g} "
              f"({r['errors']:8d} errors)  ours {r['ours']:9.3g}  ratio {r['ratio'] or float('nan'):5.2f}")
    (OUT / "paper_check.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "build":
        build()
    elif cmd == "sample":
        sample(int(float(sys.argv[2])), int(sys.argv[3]) if len(sys.argv) > 3 else 1,
               only=sys.argv[4] if len(sys.argv) > 4 else "*")
    elif cmd == "paper":
        paper(sys.argv[2], sys.argv[3])
    elif cmd == "outer":
        outer(tuple(int(x) for x in sys.argv[2:]) or (16,))
    else:
        sys.exit(__doc__)
