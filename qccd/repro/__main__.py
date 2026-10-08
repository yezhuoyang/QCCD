"""`python -m qccd.repro check [paper ...]` -- replay every artifact run in the catalog.

For each run: import the artifact's own output from `Reproduce/<paper>/artifact/`, replay it
under the paper's rule profile and duration law, and compare what the replay measures with
what the tool printed and what the paper reports.  Writes `Reproduce/<paper>/results.json`,
which is what the website shows.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from pathlib import Path

from . import catalog, importers, models
from .timed import TimedSchedule, check

ROOT = Path(__file__).resolve().parents[2] / "Reproduce"

PROFILES = {"QCCDSIM": models.QCCDSIM, "QCCDSIM_PHYSICAL": models.QCCDSIM_PHYSICAL,
            "QCCDSIM_GATESET": models.QCCDSIM_GATESET,
            "TISCC": models.TISCC, "JONES": models.JONES, "CYCLONE": models.CYCLONE}


CACHE = ROOT.parent / "out" / "repro_cache"


def _open(path: Path) -> Path:
    """A `.gz` artifact is unpacked once into `out/repro_cache` (importers read plain files)."""
    if path.suffix != ".gz":
        return path
    plain = CACHE / path.parent.parent.name / path.with_suffix("").name
    if not plain.exists() or plain.stat().st_mtime < path.stat().st_mtime:
        plain.parent.mkdir(parents=True, exist_ok=True)
        plain.write_bytes(gzip.decompress(path.read_bytes()))
    return plain


def _circuit(base: Path, spec: dict | None) -> list[tuple[str, str]] | None:
    if not spec:
        return None
    f = base / "circuits" / spec["file"]
    if spec["kind"] == "qasm":
        return importers.qasm_cx(f)
    if spec["kind"] == "stim":
        return importers.stim_cx(f)
    if spec["kind"] == "pairs":
        data = json.loads(f.read_text(encoding="utf-8"))
        # a bare list, or {"source", "sha256", "pairs"} when the list was read from a file
        # we may not ship
        return [tuple(p) for p in (data["pairs"] if isinstance(data, dict) else data)]
    raise ValueError(f"unknown circuit kind {spec['kind']!r}")


def _law_and_observers(run: dict, sched: TimedSchedule, raw: dict | None):
    law = run["law"]
    if law == "qccdsim":
        params = importers.qccdsim_params((raw or {}).get("config", {}), (raw or {}).get("device"))
        cap = next(iter(sched.device.sites.values()))["capacity"]
        an = models.QCCDSimAnalyzer(params, sched.chains, capacity=cap)
        return models.qccdsim_duration(params), [an], {"fidelity": lambda: an.fidelity,
                                                        "heating": lambda: an.heating}
    if law == "tiscc":
        return models.tiscc_duration, [], {}
    if law == "jones":
        return models.jones_duration, [], {}
    if law == "jones_6d4b467":
        return models.jones_duration_6d4b467, [], {}
    if law == "cyclone":
        return models.cyclone_duration, [], {}
    raise ValueError(f"unknown law {law!r}")


def _dig(d: dict, path):
    """`"metrics.Program Finish"`, or a list of keys when a key itself holds a dot; an
    integer step indexes a list."""
    cur = d
    for part in (path.split(".") if isinstance(path, str) else path):
        if isinstance(cur, list) and isinstance(part, int) and -len(cur) <= part < len(cur):
            cur = cur[part]
        elif isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _verdict(ours: float, theirs: float, kind: str, printed: bool = False) -> dict:
    """A tool's number and a number a paper PRINTS must be equal; a number read off a plot
    matches when it is within the plot's precision (0.5 %)."""
    rel = abs(ours - theirs) / max(abs(theirs), 1e-300)
    if kind == "tool" or printed:
        word = catalog.EXACT if rel <= 1e-9 else catalog.DIFFERS
    else:
        word = catalog.EXACT if rel <= 1e-9 else catalog.CLOSE if rel <= 5e-3 else catalog.DIFFERS
    return {"ours": ours, "theirs": theirs, "rel": rel, "verdict": word}


def check_run(paper: dict, run: dict) -> dict:
    base = ROOT / paper["key"]
    path = _open(base / "artifact" / run["file"])
    raw = None
    t0 = time.time()
    imp = run["importer"]
    if imp == "qccdsim":
        raw = json.loads(path.read_text(encoding="utf-8"))
        sched = importers.import_qccdsim(path, name=run["id"])
    elif imp == "jones":
        raw = json.loads(path.read_text(encoding="utf-8"))
        sched = importers.import_jones(path, name=run["id"])
    elif imp == "cyclone":
        raw = json.loads(path.read_text(encoding="utf-8"))
        sched = importers.import_cyclone(path, name=run["id"])
    elif imp == "tiscc":
        sched = importers.import_tiscc(path, name=run["id"], **run.get("importer_args", {}))
    else:
        raise ValueError(f"unknown importer {imp!r}")
    law, observers, extra = _law_and_observers(run, sched, raw)
    profile = PROFILES[run["profile"]]
    circ = _circuit(base, run.get("circuit"))
    rep = check(sched, profile, duration=law, circuit=circ, observers=observers)
    measured = {"makespan_us": rep.metrics["makespan_us"],
                "last_start_us": rep.metrics["last_start_us"],
                "moves": rep.metrics["counts"].get("move", 0)}
    measured.update({k: f() for k, f in extra.items()})
    compare: dict = {}
    for kind, wants in run.get("expect", {}).items():
        for metric, src in wants.items():
            if kind == "tool":
                theirs = _dig(raw or {}, src) if isinstance(src, (str, list)) else src
            else:
                theirs = src[0]
            if theirs is None or metric not in measured:
                continue
            printed = kind == "paper" and len(src) > 2 and src[2] == "printed"
            row = _verdict(float(measured[metric]), float(theirs), kind, printed)
            if kind == "paper":
                row["where"] = src[1]
                row["printed"] = printed
            compare.setdefault(kind, {})[metric] = row
    return {
        "id": run["id"], "label": run.get("label", run["id"]), "profile": profile.name,
        "checks": rep.checks, "ok": rep.ok, "violations": rep.summary()["violations"],
        "first_violations": rep.summary()["first"], "notes": dict(rep.notes),
        "measured": measured, "counts": rep.metrics["counts"], "compare": compare,
        "source": sched.source, "seconds": round(time.time() - t0, 2),
    }


def check_ours(paper: dict, o: dict, by: dict) -> dict:
    """One of our schedules, against the paper's run ``o["against"]``: the same device, that
    run's law and circuit, every profile in ``o["profiles"]``, strict durations."""
    base = ROOT / paper["key"]
    spec = next(r for r in paper["runs"] if r["id"] == o["against"])
    t0 = time.time()
    sched = TimedSchedule.load(_open(base / o["file"]))
    raw_path = _open(base / "artifact" / spec["file"])
    raw = json.loads(raw_path.read_text(encoding="utf-8")) if spec["importer"] != "tiscc" else None
    theirs = by.get(o["against"]) or {}
    circ = _circuit(base, spec.get("circuit"))
    why: list[str] = []
    if o.get("circuit_relabel") == "checks" and circ is not None:
        circ, why = importers.checks_as_scheduled(
            circ, [tuple(e.ions) for e in sched.events if e.kind == "gate"])
    ref = getattr(importers, f"import_{spec['importer']}")(raw_path, **spec.get("importer_args", {}))
    ions = lambda s: sorted(i for ch in s.chains.values() for i in ch)
    same = (sorted(sched.device.sites) == sorted(ref.device.sites)
            and all(sched.device.capacity(s) == ref.device.capacity(s) for s in ref.device.sites)
            and sorted(map(sorted, sched.device.segments.values()))
            == sorted(map(sorted, ref.device.segments.values()))
            and ions(sched) == ions(ref))
    same_ops = _same_ops(sched, ref, o.get("same_ops"))
    profiles, measured = {}, {}
    for name in o["profiles"]:
        law, observers, extra = _law_and_observers(spec, sched, raw)
        rep = check(sched, PROFILES[name], duration=law, circuit=circ, observers=observers,
                    strict=True)
        profiles[PROFILES[name].name] = {"checks": rep.checks, "ok": rep.ok,
                                         "violations": rep.summary()["violations"],
                                         "first_violations": rep.summary()["first"]}
        measured = {"makespan_us": rep.metrics["makespan_us"],
                    "last_start_us": rep.metrics["last_start_us"],
                    "moves": rep.metrics["counts"].get("move", 0)}
        measured.update({k: f() for k, f in extra.items()})
    row = {"id": o["id"], "label": o.get("label", o["id"]), "against": o["against"],
           "same_device": same, "circuit_rows": why or "true", "same_ops": same_ops,
           "profiles": profiles,
           "ok": same and not why and same_ops in ("true", "not compared")
           and all(p["ok"] for p in profiles.values()),
           "measured": measured, "theirs": theirs.get("measured", {}),
           "theirs_ok": theirs.get("ok"), "seconds": round(time.time() - t0, 2)}
    if o.get("also") and o["also"] in by:
        row["also"] = {"id": o["also"], "measured": by[o["also"]]["measured"],
                       "ok": by[o["also"]]["ok"]}
    if o.get("paper"):
        row["paper"] = o["paper"]
    return row


def _ops(sched: TimedSchedule) -> dict[str, list[tuple]]:
    """Each ion's in-place operations in time order: kind, what the paper calls it, partner."""
    out: dict[str, list[tuple]] = {}
    for e in sorted(sched.events, key=lambda e: (e.t0, e.id)):
        if e.kind in ("split", "merge", "move", "transit", "j_enter", "j_exit", "hop"):
            continue
        for i, ion in enumerate(e.ions):
            partner = tuple(x for x in e.ions if x != ion)
            out.setdefault(ion, []).append((e.kind, e.meta.get("op") or e.meta.get("name"), partner))
    return out


def _same_ops(ours: TimedSchedule, theirs: TimedSchedule, how: str | None) -> str:
    """``"sequence"``: every ion does the paper's operations, with the same partners, in the
    same order; ``"counts"``: the same operations per ion, in any order (when the paper's own
    order is the thing that is wrong).  Returns "true", "not compared", or what differs."""
    if not how:
        return "not compared"
    a, b = _ops(ours), _ops(theirs)
    if how == "counts":
        a = {k: sorted(map(repr, v)) for k, v in a.items()}
        b = {k: sorted(map(repr, v)) for k, v in b.items()}
    bad = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
    return "true" if not bad else f"{len(bad)} ion(s) differ from the paper's run, e.g. {bad[:3]}"


def check_paper(key: str) -> dict:
    p = catalog.paper(key)
    if p.get("kind") in ("design", "model"):
        # a design or model-level reproduction has no artifact runs to replay: its
        # results.json is written by its own script and must not be overwritten here
        f = ROOT / key / "results.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"runs": [], "ours": []}
    rows = []
    for run in p["runs"]:
        try:
            rows.append(check_run(p, run))
        except FileNotFoundError as ex:
            rows.append({"id": run["id"], "missing": str(ex)})
    # a paper that reports speedups: the ratio of two replayed runs against the paper's
    by = {r["id"]: r for r in rows if "missing" not in r}
    ratios = []
    for q in p.get("ratios", []):
        num, den = by.get(q["num"]), by.get(q["den"])
        if not (num and den):
            continue
        m = q.get("metric", "makespan_us")
        ours = num["measured"][m] / den["measured"][m]
        row = {"label": q["label"], "num": q["num"], "den": q["den"], "ours": ours}
        if q.get("paper"):
            row.update(_verdict(ours, float(q["paper"][0]), "paper"), where=q["paper"][1])
            row["ours"] = ours
        ratios.append(row)
    ours = [check_ours(p, o, by) for o in catalog.OURS.get(key, {}).get("runs", [])
            if (ROOT / key / o["file"]).exists()]
    out = {"paper": {k: p[k] for k in ("key", "short", "title", "authors", "venue", "year",
                                         "arxiv", "artifact", "what")},
           "runs": rows, "ratios": ratios, "ours": ours}
    dest = ROOT / key / "results.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m qccd.repro")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="replay every artifact run and write results.json")
    c.add_argument("papers", nargs="*")
    a = ap.parse_args(argv)
    keys = a.papers or [p["key"] for p in catalog.PAPERS]
    bad = 0
    for key in keys:
        if catalog.paper(key).get("kind") in ("design", "model"):
            print(f"{key:18} made by its own script; its results.json is left as it is")
            continue
        out = check_paper(key)
        for r in out.get("runs", []):
            if "missing" in r:
                print(f"{key:18} {r['id']:36} MISSING {r['missing']}")
                continue
            cmp = "; ".join(f"{kind} {m} {v['verdict']} ({v['ours']:.6g} vs {v['theirs']:.6g})"
                            for kind, ms in r["compare"].items() for m, v in ms.items())
            flag = "ok " if r["ok"] else "BAD"
            bad += 0 if r["ok"] else 1
            print(f"{key:18} {r['id']:36} {flag} {cmp}  {r['violations'] or ''}")
        for o in out.get("ours", []):
            m, t = o["measured"], o["theirs"]
            flag = "ok " if o["ok"] else "BAD"
            bad += 0 if o["ok"] else 1
            bits = [f"{k} {m[k]:.6g} vs {t[k]:.6g}" for k in ("makespan_us", "moves", "fidelity")
                    if k in m and k in t]
            print(f"{key:18} {o['id']:36} {flag} OURS {'; '.join(bits)}  "
                  + str({n: p["violations"] for n, p in o["profiles"].items() if p["violations"]} or ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
