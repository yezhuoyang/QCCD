"""Goal 3's point pages: one studio page per design point of the sweep, so every point on
board/goal3/ opens its animation, its program against the circuit, its rules and its noise.

    python goals/goal3/pages.py --out <dir> --work <work dir> [<work dir> ...] [--code bb72 ...]

A point's page is built from what the sweep left in `work/<code>/<point>/` (the device, the
compiled program, its certificate, the circuit) the way a board's seed page is
(Codesign/scripts/bb_studio.py, memory_boards.py): the studio page of the program on its device,
replayed under the release's `quantinuum` table, with the noise model's stim program block by
block (the Noise pill) and the point's numbers (the Study notes pill, whose link goes back to
the code's tab).  The replayed time must equal the sweep's row: a work folder that holds another
compile of the same point (an earlier sweep's) is refused, not drawn.  A point whose program
breaks a rule gets its page too, to show where; a refused size has no program and no page.
The sweep skips the Lean step, and so do the pages: R10 is not claimed on them.

Work folders are found under each `--work` root in turn (the sweep ran in several trees); the
pages land in `<out>/<code>/<point>.html` with `<out>/pages.json`, which the site reads.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (ROOT, ROOT / "Codesign" / "scripts", HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

TABLE = "quantinuum"


def rows(code: str) -> list[dict]:
    """The code's results, the last line of a point winning (as plot.py and the site read them)."""
    seen: dict = {}
    for ln in (HERE / "results" / f"{code}.jsonl").read_text(encoding="utf-8").splitlines():
        if ln.strip():
            r = json.loads(ln)
            seen[(r["family"], r["point"])] = r
    return list(seen.values())


def find_work(code: str, point: str, roots: list[Path]) -> Path | None:
    for r in roots:
        w = r / code / point
        if (w / "prog.cooled.tsir.json").is_file():
            return w
    return None


def build(row: dict, work: Path, rel, exp, out: Path, *, max_frames: int = 20000, gz: bool = False) -> dict:
    import bb_studio as B
    import memory_boards as M
    from qccd.arch import load
    from qccd.compile.decode import insert_decodes
    from qccd.cost.models import corrected_model
    from qccd.ir.tsir import TSIR
    from qccd.verify import verify
    from qccd.viz.render import render_html

    arch = load(str(work / "device.arch.json"))
    prog = insert_decodes(TSIR.load(str(work / "prog.cooled.tsir.json")))
    cert = json.loads((work / "prog.qcert.json").read_text(encoding="utf-8"))
    source = B.build_source(prog, cert, work / "circuit.qasm")
    model = corrected_model(TABLE)
    report = verify(prog, arch, model, check_metrics=False)
    t_ms = report.result.total_us / 1000.0
    if abs(t_ms - row["T_ms"]) > 1e-6 * max(1.0, row["T_ms"]):
        raise ValueError(f"{work}: replays to {t_ms} ms, the sweep's row says {row['T_ms']} ms")
    failed = report.rules.summary()["failed"]
    rounds = 2 * exp.rounds
    if row["status"] == "ok":
        ler = row["ler"]
        claim = (f"{row['family']} {row['point']}: {t_ms / rounds:.1f} ms per round over {rounds} rounds "
                 f"({t_ms:.1f} ms in all, {TABLE} table); LER per round {ler['per_round']:.2e}, 95% interval "
                 f"{ler['per_round_lo']:.1e} to {ler['per_round_hi']:.1e} ({ler['errors']} logical errors in "
                 f"{ler['shots']:,} shots)")
    else:
        claim = f"{row['family']} {row['point']}: breaks {', '.join(failed) or row.get('rules_failed')}. {row.get('why', '')}"
    lede = (f"{claim}  |  {len(prog)} instructions against {len(source['ops'])} circuit statements; rules "
            f"{'all pass' if not failed else 'FAILED ' + ' '.join(failed)}")
    page = out / row["code"] / f"{row['point']}.html"
    page.parent.mkdir(parents=True, exist_ok=True)
    render_html(B._publishable(arch), prog, report.result, model, page,
                kicker=f"GOAL 3 · {rel.title.removeprefix('Goal 3: ').upper()}",
                headline=f"{rel.title} on {row['point']}", lede=lede, source=source,
                template_stems="*", max_frames=max_frames)
    numbers = {"compiled": f"compiled by {'the baseline C2LR router' if row.get('c2lr') else 'qccdc b4a003dbb4ae'}; "
                           f"the sweep skips the Lean step, so R10 is not claimed here"}
    # the page is served as board/goal3/p/<code>/<point>.html: Goal 3's page is two folders up
    B._inject(page, {"key": row["point"], "claim": claim}, [], numbers, f"../../#{rel.manifest['task']}")
    if row["status"] == "ok":
        lr = json.loads((work / "ler_report.json").read_text(encoding="utf-8"))
        panel = M.noise_panel({"prefix": str(work / "prog"), "cooled": str(work / "prog.cooled.tsir.json"),
                               "arch": str(work / "device.arch.json"), "ler": row["ler"], "budget": lr["budget"],
                               "stats": lr["stats"], "key": f"{row['code']}_{row['point']}"}, rel, exp)
        # the panel links as a board's seed page does (board/<task>/<page>); this one is two deeper
        for a, b in (('href="../noise/"', 'href="../../../noise/"'),
                     ('href="../../evaluation/"', 'href="../../../../evaluation/"')):
            assert panel.count(a) == 1, a
            panel = panel.replace(a, b)
        src = page.read_text(encoding="utf-8")
        page.write_text(src.replace("</body>", panel + "</body>", 1), encoding="utf-8", newline="")
    size = page.stat().st_size
    if gz:
        # stored compressed only: nginx serves board/goal3/p/ with gzip_static (a 45 MB BB page is 3 MB)
        import gzip
        page.with_name(page.name + ".gz").write_bytes(gzip.compress(page.read_bytes(), 9, mtime=0))
        page.unlink()
    return {"code": row["code"], "family": row["family"], "point": row["point"], "status": row["status"],
            "page": f"{row['code']}/{row['point']}.html", "bytes": size,
            "instructions": len(prog), "rules_failed": failed}


def _one(code: str, row: dict, work: Path, out: Path, gz: bool) -> dict:
    import sweep
    rel = sweep.release(code)
    return build(row, work, rel, sweep.load_exp(rel), out, gz=gz)


def main() -> int:
    from concurrent.futures import ProcessPoolExecutor, as_completed
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--work", type=Path, nargs="+", required=True)
    ap.add_argument("--code", nargs="*", default=[])
    ap.add_argument("--point", nargs="*", default=[])
    ap.add_argument("--gzip", action="store_true", help="keep only <page>.html.gz, as the site serves them")
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()
    codes = a.code or sorted(p.stem for p in (HERE / "results").glob("*.jsonl"))
    index_p = a.out / "pages.json"
    index = json.loads(index_p.read_text(encoding="utf-8")) if index_p.exists() else {}
    todo = []
    for code in codes:
        for row in rows(code):
            if row["status"] not in ("ok", "rules_failed") or (a.point and row["point"] not in a.point):
                continue
            w = find_work(code, row["point"], a.work)
            if w is None:
                print(f"{code} {row['point']}: no work folder", flush=True)
                continue
            todo.append((code, row, w))
    bad = 0
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(_one, code, row, w, a.out, a.gzip): (code, row["point"]) for code, row, w in todo}
        for f in as_completed(futs):
            code, point = futs[f]
            try:
                m = f.result()
            except Exception as exc:  # noqa: BLE001 -- a point that cannot be drawn is reported, not drawn
                bad += 1
                print(f"{code:9s} {point:18s} NOT BUILT: {type(exc).__name__}: {exc}"[:300], flush=True)
                continue
            index.setdefault(code, {})[point] = m
            print(f"{code:9s} {point:18s} {m['status']:12s} {m['bytes'] / 1e6:6.1f} MB", flush=True)
            index_p.write_text(json.dumps(index, indent=1, sort_keys=True), encoding="utf-8")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
