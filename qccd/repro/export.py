"""A timed schedule as a file a reader can check by hand.

`TimedSchedule.to_json` is the checker's input: events with start and end times, and the
machine they run on.  What a reader re-deriving a runtime or a speedup also needs is spelled
out here, so nothing has to be recomputed to be checked: for every event, the zones, segments
and junctions it holds over its interval, and for every zone, which ion stood in it from
when to when.  Both are derived the way `timed.check` derives them (a move holds every
segment and junction on its path for its whole interval, and its ion counts in the zone it
left until the move ends and in the zone it reaches from the move's start), and
`tests/test_repro_studio.py` holds the two to the same verdicts.

Only schedules whose transport is a whole-move `hop` are written (TISCC's and ours on its
grid); a split/move/merge schedule is refused rather than flattened.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from .timed import Report, TimedSchedule

__all__ = ["PAPERS", "timeline", "dumps", "paper_timelines"]

#: papers whose page offers its timed schedules, theirs and ours, as files
PAPERS = ("leblond2023",)

#: what each field means, written into the file itself
READING = {
    "times": "microseconds; every interval is closed-open, [t0, t1)",
    "events": "one per operation, in start-time order: id, kind, the operation as the tool names it, "
              "start t0, end t1, the ions it acts on, and the zones, segments and junctions it holds "
              "for its whole interval",
    "residency": "zone -> [ion, from, to]: the ion stood in the zone from `from` to `to` (null: the "
                 "start or the end of the schedule); a moving ion counts in the zone it left until the "
                 "move ends and in the zone it reaches from the move's start",
    "law": "the duration of each operation, as the paper's timing law gives it (TISCC: a move to "
           "a neighbouring zone is one Move, a move through a junction Move + Junction)",
    "device": "zones (M or O, with their capacity), junctions, the segments joining them, and "
              "for each zone the zones a two-qubit gate partner may stand in",
    "rules": "the paper's rules the schedule was checked under, and the verdicts",
}


def _segments_on(sched: TimedSchedule, walk: list[str]) -> list[str]:
    out = []
    for a, b in zip(walk, walk[1:]):
        hit = [k for k, ends in sched.device.segments.items() if set(ends) == {a, b}]
        if len(hit) != 1:
            raise ValueError(f"{len(hit)} segments join {a} and {b}")
        out.append(hit[0])
    return out


def timeline(sched: TimedSchedule, *, title: str, by: str, law: Mapping[str, float],
             report: Report, notice: str | None = None) -> dict[str, Any]:
    """`sched` with every event's held resources and every zone's residency made explicit,
    plus the law and the verdicts `report` (`timed.check`) gave it."""
    dev = sched.device
    zone_of: dict[str, str] = {}
    since: dict[str, float | None] = {}
    residency: dict[str, list[list]] = {s: [] for s in dev.sites}
    for site, ions in sched.chains.items():
        for ion in ions:
            zone_of[ion], since[ion] = site, None
    events = []
    for e in sorted(sched.events, key=lambda e: (e.t0, e.id)):
        row: dict[str, Any] = {"id": e.id, "kind": e.kind, "op": e.meta.get("op"),
                               "t0": e.t0, "t1": e.t1, "ions": list(e.ions)}
        if e.kind == "hop":
            ion = e.ions[0]
            if zone_of.get(ion) != e.src:
                raise ValueError(f"event {e.id}: {ion} is in {zone_of.get(ion)}, not {e.src}")
            walk = [e.src, *e.path, e.dst]
            row.update(zones=[e.src, e.dst], segments=_segments_on(sched, walk),
                       junctions=list(e.path))
            residency[e.src].append([ion, since[ion], e.t1])
            zone_of[ion], since[ion] = e.dst, e.t0
        elif e.kind in ("gate", "gate1", "prepare", "measure", "reset", "cool"):
            row.update(zones=sorted({zone_of[i] for i in e.ions}, key=str), segments=[],
                       junctions=[])
        else:
            raise ValueError(f"event {e.id}: a {e.kind!r} event; only hop schedules are written")
        events.append(row)
    for ion, site in zone_of.items():
        residency[site].append([ion, since[ion], None])
    rules = {"profile": report.profile, "checks": dict(report.checks),
             "violations": len(report.violations)}
    out = {
        "format": "qccd timed schedule, version 1",
        "schedule": title,
        "by": by,
        "reading": READING,
        "makespan_us": max((e.t1 for e in sched.events), default=0.0),
        "law": dict(law),
        "rules": rules,
        "device": {
            "zones": {s: {k: a[k] for k in ("zone", "capacity") if k in a} for s, a in dev.sites.items()},
            "junctions": sorted(dev.nodes, key=lambda n: (0, int(n), "") if n.isdigit() else (1, 0, n)),
            "segments": {k: list(v) for k, v in dev.segments.items()},
            "gate_neighbours": dev.adjacent,
        },
        "initial": {s: list(c) for s, c in sched.chains.items() if c},
        "events": events,
        "residency": {s: r for s, r in residency.items() if r},
    }
    if notice:
        out["notice"] = notice
    return out


def _tiscc_notice(base, run: dict) -> str:
    """TISCC's license asks a derivative to say so, and to carry the license and its
    acknowledgment (`Reproduce/leblond2023/artifact/LICENSE`, `NOTICE.md`)."""
    lic = (base / "artifact" / "LICENSE").read_text(encoding="utf-8").strip()
    cmd = (base / "artifact" / f"{run['id']}.case.sh").read_text(encoding="utf-8").strip()
    return (f"Converted from TISCC's own output: the time-resolved schedule TISCC prints for "
            f"`{cmd}`, as shipped with TISCC's regression tests (github.com/ORNL-QCI/TISCC, commit "
            "1212aecf58b8cad7b3cd0a8069d596d147af37af). Changed by the QCCD reproduction "
            "(qccd.academy): the start times, operations and zones are TISCC's; each end time is "
            "the start plus TISCC's own duration for the operation; the segments, junctions and "
            "residency are read off TISCC's grid. This product includes software produced by "
            "UT-Battelle, LLC under Contract No. DE-AC05-00OR22725 with the Department of Energy."
            "\n\n" + lic)


def paper_timelines(key: str) -> list[tuple[str, dict[str, Any]]]:
    """(run id, `timeline`) for every replayed run of `key` and every schedule of ours, each
    with the verdicts the checker gives it under the paper's rules (`python -m qccd.repro
    check`: the paper's runs as the artifact wrote them, ours with strict durations)."""
    from . import catalog, importers, models
    from .__main__ import PROFILES, ROOT, _circuit, _law_and_observers, _open
    from .timed import check

    laws = {"tiscc": models.TISCC_US}
    p = catalog.paper(key)
    base = ROOT / key
    out = []
    for run in p.get("runs", []):
        if run["importer"] != "tiscc":
            raise ValueError(f"{key}/{run['id']}: only TISCC's hop schedules are written")
        sched = importers.import_tiscc(_open(base / "artifact" / run["file"]), name=run["id"],
                                       **run.get("importer_args", {}))
        law, observers, _ = _law_and_observers(run, sched, None)
        rep = check(sched, PROFILES[run["profile"]], duration=law,
                    circuit=_circuit(base, run.get("circuit")), observers=observers)
        out.append((run["id"], timeline(
            sched, title=run.get("label", run["id"]), law=laws[run["law"]], report=rep,
            by=f"{p['short']}: its own schedule, as its code printed it",
            notice=_tiscc_notice(base, run))))
    for o in catalog.OURS.get(key, {}).get("runs", []):
        spec = next(r for r in p["runs"] if r["id"] == o["against"])
        sched = TimedSchedule.load(_open(base / o["file"]))
        law, observers, _ = _law_and_observers(spec, sched, None)
        rep = check(sched, PROFILES[o["profiles"][0]], duration=law,
                    circuit=_circuit(base, spec.get("circuit")), observers=observers, strict=True)
        out.append((o["id"], timeline(
            sched, title=f"{o.get('label', o['id'])}: our schedule", law=laws[spec["law"]],
            report=rep, by=f"ours: the same operations as {p['short']}'s run, on its grid, "
                           f"under its rules (strict durations)")))
    return out


def dumps(doc: Mapping[str, Any]) -> str:
    """JSON with one event and one zone per line: small, and readable in a browser tab."""
    head = {k: v for k, v in doc.items()
            if k not in ("device", "initial", "events", "residency", "notice")}
    parts = [json.dumps(head, indent=1, ensure_ascii=False)[:-2]]
    for key in ("device", "initial"):
        body = ",\n".join(f"  {json.dumps(k)}: " + json.dumps(v, separators=(",", ":"))
                          for k, v in doc[key].items())
        parts.append(f',\n "{key}": {{\n' + body + "\n }")
    ev = ",\n".join("  " + json.dumps(e, separators=(",", ":")) for e in doc["events"])
    parts.append(',\n "events": [\n' + ev + "\n ]")
    rs = ",\n".join(f"  {json.dumps(z)}: " + json.dumps(r, separators=(",", ":"))
                    for z, r in doc["residency"].items())
    parts.append(',\n "residency": {\n' + rs + "\n }")
    if "notice" in doc:
        parts.append(',\n "notice": ' + json.dumps(doc["notice"], ensure_ascii=False))
    return "".join(parts) + "\n}\n"
