"""Shared planes: devices that share one plane of DACs, or one laser.  docs/GADGETS.md §4.3.

Every leaf declares a control plane of its own (32 broadcast channels and a hold bank, a
switch per site; a global beam with a switch per zone).  A floorplan may say that several
devices share one instead (`plane NAME dacs|lasers MEMBER ...`, floorplan.py): fewer DACs
or fewer beams, and the price is the rule a plane has always had -- one waveform at a time
(R4, R22), one pulse at a time.  Two devices on one plane may play a cycle at once only if it
is the same cycle: the same class (or gate), the same direction in the lab, the same start
and end.  Anything else is two waveforms on one set of channels.

The scheduler keeps it by turns: the members of a plane share one calendar, so one of them
works at a time (city.py, `_share_planes`), and a trip over two roads of one plane waits
for the first to be done before the second loads (store-and-forward, `City._road`).
`check_planes` (rule H2) reads the composed program -- every op's TSIR cycles at their
times, every road's compiled program (roads.py) -- and judges every instant.
"""
from __future__ import annotations

__all__ = ["declared", "resolve", "check_planes"]


def declared(lib) -> list:
    """[(name, kind, members)] as the floorplan wrote them."""
    top = lib[lib.top]
    return [tuple(x) for x in (top.params.get("planes") or [])]


def resolve(lib, families: dict, nets) -> dict:
    """{plane: {"kind", "places": [...], "nets": [...], "group": n}}: each plane's member
    devices by path.  `families`: place path -> its master's family (a junction is "road");
    `nets`: the road paths ("/door_prep_in").  Planes that share a device are one group --
    a device can only take turns on one calendar."""
    nets = list(nets)
    out: dict = {}
    for name, kind, members in declared(lib):
        places, roads = set(), set()
        for m in members:
            if m == "roads":
                roads.update(nets)
            elif m == "junctions":
                places.update(p for p, f in families.items() if f == "road")
            elif m in families:
                places.add(m)
            elif m in nets or "/" + m in nets:
                roads.add(m if m in nets else "/" + m)
            elif any(f == m for f in families.values()):
                places.update(p for p, f in families.items() if f == m)
            else:
                raise ValueError(f"plane {name}: no place, road, junction or family named {m!r}")
        out[name] = {"kind": kind, "places": sorted(places), "nets": sorted(roads)}
    # union the planes that share a device: they are one exclusive group
    parent = {n: n for n in out}

    def find(n):
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n
    owner: dict = {}
    for n, pl in out.items():
        for d in pl["places"] + pl["nets"]:
            if d in owner:
                parent[find(n)] = find(owner[d])
            else:
                owner[d] = n
    groups = sorted({find(n) for n in out})
    for n, pl in out.items():
        pl["group"] = groups.index(find(n))
    return out


def _leaf_cycles(gir, lib, leaves, path, kind):
    """(t0, t1, signature) of every cycle of every op run at place `path` that plays on a
    plane of `kind`: transport cycles for `dacs`, gates, measurements, resets and cools for
    `lasers`.  A transport cycle's signature is its class and the lab directions of its
    hops (a place stands untransformed on the floor, so its device's axes are the lab's)."""
    m = lib[gir["leaves"][path][0]]
    d = leaves.data.get(m.name) or {}
    progs, times = d.get("programs") or {}, d.get("times") or {}
    pos = {n["id"]: n["pos"] for n in (d.get("device") or {}).get("nodes", [])}
    out = []
    for e in gir["events"]:
        if e[3] != path or e[4] not in progs:
            continue
        span = {row[0]: (row[1], row[2]) for row in times.get(e[4], [])}
        for ins in progs[e[4]]["instructions"]:
            t = span.get(ins["id"])
            if t is None or t[1] <= t[0]:
                continue
            typ = ins["type"]
            if kind == "dacs" and typ == "simd":
                dirs = set()
                for q in ins.get("participants") or []:
                    a, b = pos.get(q.get("from") or q.get("src")), pos.get(q.get("to") or q.get("dst"))
                    if a and b:
                        dirs.add(((b[0] > a[0]) - (b[0] < a[0]), (b[1] > a[1]) - (b[1] < a[1])))
                sig = ("simd", ins.get("cls"), tuple(sorted(dirs)) or ("template",))
            elif kind == "lasers" and typ in ("gate", "measure", "reset", "cool"):
                sig = (typ, ins.get("gate"))
            else:
                continue
            out.append((e[1] + t[0], e[1] + t[1], sig))
    return out


def check_planes(gir: dict, lib, leaves, runs: dict | None = None) -> dict:
    """Rule H2: no two members of a plane ever play different cycles at once.  `runs`:
    carry id -> its compiled road program (roads.check_roads), for roads on a plane."""
    planes = gir.get("planes") or {}
    violations: list = []
    metrics = {"planes": len(planes), "members": 0, "cycles": 0}
    by_net: dict = {}
    for c in gir["carries"]:
        by_net.setdefault(c[1], []).append(c)
    for name, pl in planes.items():
        cycles = []
        for p in pl["places"]:
            if p in gir["leaves"]:
                cycles += [(a, b, sig, p) for a, b, sig in _leaf_cycles(gir, lib, leaves, p, pl["kind"])]
        if pl["kind"] == "dacs":
            for net in pl["nets"]:
                for c in by_net.get(net, []):
                    run = (runs or {}).get(c[0])
                    if run is None:
                        continue
                    for iid, a, b in run.times:
                        ins = next((i for i in run.prog.instructions if i.id == iid), None)
                        # a road's own frame is not the lab's: its cycles share with no one
                        cycles.append((c[4] + a, c[4] + b, ("simd", ins.cls if ins else "?", net), net))
        metrics["members"] += len(pl["places"]) + len(pl["nets"])
        metrics["cycles"] += len(cycles)
        cycles.sort(key=lambda x: (x[0], x[1]))
        active: list = []
        bad = 0
        for a, b, sig, dev in cycles:
            active = [x for x in active if x[1] > a + 1e-9]
            for a2, b2, sig2, dev2 in active:
                if dev2 == dev:
                    continue
                if abs(a2 - a) < 1e-6 and abs(b2 - b) < 1e-6 and sig2 == sig:
                    continue                      # the same cycle, played once for both
                bad += 1
                if len(violations) < 20:
                    violations.append(f"plane {name}: {dev} plays {sig[0]} {sig[1]} at {a:.1f}-{b:.1f} µs "
                                      f"while {dev2} plays {sig2[0]} {sig2[1]} at {a2:.1f}-{b2:.1f} µs")
            active.append((a, b, sig, dev))
        if bad > 20 and len(violations) == 20:
            violations.append(f"plane {name}: ... {bad - 20} more")
    return {"violations": violations, "metrics": metrics}
