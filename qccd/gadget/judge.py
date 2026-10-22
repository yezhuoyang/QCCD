"""Judging a floorplan and a schedule by the machine's rules, and measuring its traffic.

    from qccd.gadget.judge import judge_floorplan, judge_schedule, traffic
    judge_floorplan(lib, leaves)                -> the F-rules' report (docs/ARCHITECTURE.md §3)
    judge_schedule(lib, leaves, gir)            -> F- and G-rules together (§4)
    traffic(gir)                                -> what the schedule shuttles, and how parallel it is

The rules are text (`qccd/ruleset/library/floorplan.rules`, `machine.rules`) evaluated by the
rule language; nothing here decides a rule.  `traffic` only counts.
"""

from __future__ import annotations

from collections import defaultdict

from ..ruleset import check, load
from ..ruleset.machine import CLASSICAL_FAMILIES, machine_execution

__all__ = ["judge_floorplan", "judge_schedule", "traffic"]


def _report_json(rep) -> dict:
    out = {"ruleset": rep.ruleset, "title": rep.title, "passed": [], "failed": [],
           "not_applicable": {}, "unchecked": {}, "rules": []}
    for r in rep.results:
        row = r.to_json()
        row["text"] = getattr(r, "text", "")
        row["about"] = getattr(r, "about", "")
        out["rules"].append(row)
        if r.verdict == "holds":
            out["passed"].append(r.id)
        elif r.verdict == "fails":
            out["failed"].append(r.id)
        elif r.verdict == "does not apply":
            out["not_applicable"][r.id] = r.reason
        else:
            out["unchecked"][r.id] = r.reason
    out["ok"] = not out["failed"] and not out["unchecked"]
    return out


def judge_floorplan(lib, leaves, *, regions: dict | None = None) -> dict:
    """The floorplan rules, on the floorplan alone."""
    exe = machine_execution(lib, leaves, regions=regions)
    return _report_json(check(load("floorplan"), exe))


def judge_schedule(lib, leaves, gir: dict, *, regions: dict | None = None) -> dict:
    """Every machine rule -- the floorplan's and the schedule's -- on one schedule."""
    exe = machine_execution(lib, leaves, gir, regions=regions)
    rs = load("machine")
    fp = load("floorplan")
    rep = check(fp, exe)
    rep2 = check(rs, exe)
    rep.results += rep2.results
    rep.ruleset, rep.title = "machine", rs.title
    return _report_json(rep)


def traffic(gir: dict, lib=None) -> dict:
    """How much the schedule shuttles, how long bundles wait, and how much runs at once."""
    nets = gir["nets"]
    ion_sites = 0
    waits = []
    busy: dict[str, float] = defaultdict(float)
    for c in gir["carries"]:
        length = int(nets[c[1]][4])
        ion_sites += len(c[7]) * length
        tau = float(c[6])
        w = max([float(k) - (float(d) + tau) for d, k in zip(c[5], c[12])] + [0.0])
        waits.append(w)
        busy[c[1]] += float(c[11]) - float(c[4])
    masters = {p: v[0] for p, v in gir["leaves"].items()}
    fam = {}
    if lib is not None:
        fam = {p: lib[m].family for p, m in masters.items()}
    passes = sum(1 for e in gir["events"] if masters[e[3]].startswith("junc"))
    # concurrency: how many places (not junctions, not the classical half) run an op at once
    marks = []
    for e in gir["events"]:
        m = masters[e[3]]
        if m.startswith("junc") or fam.get(e[3]) in CLASSICAL_FAMILIES or m in ("dec_d3", "arch1"):
            continue
        if float(e[2]) > float(e[1]):
            marks += [(float(e[1]), 1), (float(e[2]), -1)]
    level = peak = 0
    weighted = 0.0
    last = 0.0
    for t, d in sorted(marks, key=lambda x: (x[0], x[1])):
        weighted += level * (t - last)
        last = t
        level += d
        peak = max(peak, level)
    span = float(gir["makespan_us"]) or 1.0
    return {
        "ion_sites": ion_sites,
        "carries": len(gir["carries"]),
        "junction_passes": passes,
        "wait_us": round(sum(waits), 3),
        "longest_wait_us": round(max(waits + [0.0]), 3),
        "busiest_road": max(busy, key=busy.get).lstrip("/") if busy else None,
        "busiest_road_share": round(max(busy.values(), default=0.0) / span, 4),
        "peak_parallel_ops": peak,
        "mean_parallel_ops": round(weighted / span, 3),
        "makespan_us": gir["makespan_us"],
    }
