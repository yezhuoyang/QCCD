"""Goal 3's "Download and reproduce" zips: one per design point of the sweep.

    python goals/goal3/bundles.py --out <dir> --work <work dir> [<work dir> ...]
                                  [--code bb72 ...] [--point NAME ...] [--workers 1]

Every compiled point of board/goal3/ gets one zip (`qccd.qec.reproduce`, the builder every board
uses): the code's circuit, the point's machine, its hardware program and certificate, the noisy
stim circuit the sweep sampled (the sweep's noise model applied to the program), the decoder and
the budget the row was sampled with (a row's own batch size included), `run.py`, which samples
and decodes it again with nothing but stim, numpy and the decoder, and `regenerate.py`, which
replays the program and rebuilds the stim circuit with the qccd package.

A zip is made from what the sweep left in `work/<code>/<point>/`.  Work folders are looked for
under each `--work` root in turn (the sweep ran in several trees), and a folder is the point's
only if its program replays to the row's time: a folder that holds another compile of the point
(an earlier sweep's) is refused, as pages.py refuses to draw it, and the next root is tried.

The zips land in `<out>/<code>/<point>.zip`, served as board/goal3/z/<code>/<point>.zip, and
`goals/goal3/bundles.json` (committed; small) records each one, so the Goal 3 page links every
point and pages.py gives the point's own page its button.  A BB72 program is 19 MB of JSON and
its extraction takes gigabytes: one worker, a process per point.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (ROOT, HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

TABLE = "helios"
INDEX = HERE / "bundles.json"
SITE = "https://qccd.academy/board/goal3/"


def rows(code: str) -> list[dict]:
    """The code's results, the last line of a point winning (as plot.py, pages.py and the site read them)."""
    seen: dict = {}
    for ln in (HERE / "results" / f"{code}.jsonl").read_text(encoding="utf-8").splitlines():
        if ln.strip():
            r = json.loads(ln)
            seen[(r["family"], r["point"])] = r
    return list(seen.values())


def candidates(code: str, point: str, roots: list[Path]) -> list[Path]:
    """Every work folder that holds a compile of the point, in the roots' order."""
    return [r / code / point for r in roots if (r / code / point / "prog.cooled.tsir.json").is_file()]


def same_sample(row: dict, work: Path) -> bool | None:
    """Whether the folder's own report is the row's sample (None: the folder has no report).
    A cheap first look; the replayed time, checked when the zip is built, is what decides."""
    f = work / "ler_report.json"
    if not f.is_file() or row.get("status") != "ok":
        return None
    got = (json.loads(f.read_text(encoding="utf-8")).get("ler") or {})
    want = row["ler"]
    return (got.get("errors"), got.get("shots"), got.get("seed")) == (want["errors"], want["shots"], want["seed"])


def peak_gb() -> float | None:
    """This process's peak memory, in GB (None where the system does not say): a BB72 point's
    extraction is the heavy step, and the log says what it took."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            c = Counters()
            c.cb = ctypes.sizeof(c)
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            if not psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
                return None
            return max(c.PeakWorkingSetSize, c.PeakPagefileUsage) / 2 ** 30
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak / (2 ** 30 if sys.platform == "darwin" else 2 ** 20)
    except Exception:  # noqa: BLE001 -- a number for the log, never a reason to fail
        return None


def point_of(row: dict, work: Path):
    """The sweep's row and its work folder as the builder's `Point`."""
    from qccd.qec.reproduce import Point
    from qccd.workspace.tasks import find_release
    rel = find_release(row["release"])
    ok = row["status"] == "ok"
    tools = None
    if ok and (work / "ler_report.json").is_file():
        tools = json.loads((work / "ler_report.json").read_text(encoding="utf-8")).get("tools")
    cert = work / "prog.qcert.json"
    broke = ", ".join(row.get("rules_failed") or [])
    return Point(
        board=f"goal3-{row['code']}", board_title=rel.title, entry=row["point"],
        title=f"{rel.title} on {row['point']} ({row['family']})",
        qasm=work / "circuit.qasm", machine=work / "device.arch.json",
        program=work / "prog.cooled.tsir.json", certificate=cert if cert.is_file() else None,
        table=TABLE, release=rel.id, noise=row.get("noise"),
        budget=row.get("ler_budget") if ok else None, ler=row["ler"] if ok else None, tools=tools,
        time_ms=row["T_ms"], time_tol_ms=1e-6 * max(1.0, row["T_ms"]),
        rule_set=row.get("rule_set"), replay_rules=False, rules_failed=row.get("rules_failed") or [],
        machine_name=row["point"], family=row["family"], compiler=row.get("compiler", ""),
        physics=row.get("physics", ""), url=f"{SITE}#{rel.manifest['task']}",
        numbers={k: row[k] for k in ("T_ms", "T_round_ms", "ions", "instructions", "cools", "transport_steps")
                 if k in row},
        note=("The reference sweep judges a family by its own rule set and skips the certificate's Lean "
              "step; an entry submitted to the official server is graded with it."),
        no_ler_reason=("" if ok else
                       f"this size's program breaks {broke} of its family's rule set, so the sweep did not sample "
                       "its logical error rate: there is no stim circuit, and replaying the hardware program "
                       "(its time, and the rules it breaks) is the whole reproduction"))


def build(row: dict, works: list[str], out: str) -> dict:
    """One point's zip from the first work folder whose program is the row's; the index entry."""
    from qccd.qec.reproduce import ReproduceError, decoder_label, write
    # the folders whose own report is the row's sample first, then those without a report
    rank = {True: 0, None: 1, False: 2}
    order = sorted((Path(w) for w in works), key=lambda w: rank[same_sample(row, w)])            # stable
    refused = []
    for work in order:
        try:
            info = write(point_of(row, work), Path(out) / row["code"] / f"{row['point']}.zip")
        except ReproduceError as exc:
            refused.append(str(exc)[:160])
            continue
        # the index entry is a function of the row alone (a zip's bytes name the commit that built it)
        return {"zip": f"{row['code']}/{row['point']}.zip", "name": info["name"], "reproduces": info["reproduces"],
                **({"decoder": decoder_label(row["ler"], row.get("ler_budget"))} if row["status"] == "ok" else {}),
                "_bytes": info["bytes"], "_seconds": info["seconds"], "_peak_gb": peak_gb()}
    raise ReproduceError(f"{row['code']} {row['point']}: no work folder holds this row's program ("
                         + "; ".join(refused) + ")")


def main() -> int:
    from concurrent.futures import ProcessPoolExecutor, as_completed
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--work", type=Path, nargs="+", required=True)
    ap.add_argument("--code", nargs="*", default=[])
    ap.add_argument("--point", nargs="*", default=[])
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--index", type=Path, default=INDEX, help="where the index is kept (default: beside this script)")
    a = ap.parse_args()
    codes = a.code or sorted(p.stem for p in (HERE / "results").glob("*.jsonl"))
    index = json.loads(a.index.read_text(encoding="utf-8")) if a.index.exists() else {}
    todo = []
    for code in codes:
        for row in rows(code):
            if row["status"] not in ("ok", "rules_failed") or (a.point and row["point"] not in a.point):
                continue
            works = candidates(code, row["point"], a.work)
            if not works:
                print(f"{code:9s} {row['point']:18s} no work folder", flush=True)
                continue
            todo.append((row, [str(w) for w in works]))
    bad = 0
    t0 = time.time()
    # a process per point: a long programme's extraction takes gigabytes, and a worker that goes on keeps them
    one_each = {"max_tasks_per_child": 1} if sys.version_info >= (3, 11) else {}
    with ProcessPoolExecutor(max_workers=a.workers, **one_each) as ex:
        futs = {ex.submit(build, row, works, str(a.out)): row for row, works in todo}
        for f in as_completed(futs):
            row = futs[f]
            try:
                m = f.result()
            except Exception as exc:  # noqa: BLE001 -- a point that cannot be built is reported, not built
                bad += 1
                print(f"{row['code']:9s} {row['point']:18s} NOT BUILT: {type(exc).__name__}: {exc}"[:400], flush=True)
                continue
            size, secs, peak = m.pop("_bytes"), m.pop("_seconds"), m.pop("_peak_gb")
            index.setdefault(row["code"], {})[row["point"]] = m
            print(f"{row['code']:9s} {row['point']:18s} {m['reproduces']:5s} {size / 1e3:9.1f} KB {secs:7.1f} s"
                  + (f"  peak {peak:5.2f} GB" if peak is not None else ""), flush=True)
            a.index.write_text(json.dumps(index, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    n = sum(len(v) for v in index.values())
    print(f"{len(todo) - bad} of {len(todo)} built in {time.time() - t0:.0f} s; the index names {n} points", flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
