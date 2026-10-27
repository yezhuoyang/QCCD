"""A road's own hardware program: every carry compiled onto its conveyor and judged by the
basic rules.  docs/GADGETS.md §4.2.

A gadget is a box with doors, but the machine is one chip, and what moves ions between the
boxes obeys the same rules as what moves them inside one: a cycle is one waveform on one
plane of DACs (R4, R22), a site holds what it holds (R1), a site that sits a cycle out needs
its switch (R4d), and so on (`docs/rules.md`).  The schedule prices a road from its
conveyor's one-ion program, and an ion's trip as that program run once per ion.  That is not
a program a road can play when a bundle is on it: two ions entering a long road a cycle apart
would ask its electrodes for a load and a shift at once, and ions arriving at the far end
before the one ahead is taken would stack on the end stub.

So every carry is compiled here into ONE program on the road's own device (`chan<L>`, its
stubs as wide as the bundle):

* one class a cycle -- a load off the near stub, a conveyor shift, or a delivery onto the
  far stub -- each the waveform its class plays;
* a shift is rigid: every ion on the rail that can advance does; one blocked by the ion
  ahead holds (its site's switch);
* an ion is loaded no earlier than the op on the near side put it on the stub, and never in
  a cycle in which that op is moving an ion onto it: a door's stub is driven by one side at a
  time;
* likewise at the far end: no delivery while the op there takes an ion off the stub.

`compile_carry` returns the program, its cycles' times, when each ion reached the far stub,
and the verifier's verdict on the program (R1-R23).  A delivery later than the schedule's
take is a violation: the schedule asked the road for something it cannot do.
"""
from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["compile_carry", "RoadRun", "conveyor_machine", "road_cycle_us", "check_roads"]


_MACHINES: dict = {}
_CYCLE: dict = {}
_RUNS: dict = {}


def conveyor_machine(shape, width: int):
    """The device a carry of `width` ions runs on, on a road of this shape (its sites when
    straight, else its legs): the road's conveyor, its two stubs each holding the bundle
    (they are the doors' waiting places)."""
    key = (str(shape), max(1, int(width)))
    if key not in _MACHINES:
        from .leaves.transport import road
        _MACHINES[key] = road(*key).machine
    return _MACHINES[key]


def road_cycle_us(shape) -> dict:
    """How long each class's cycle takes on a road of this shape, from the replay of its
    one-ion program under the cost model every leaf is priced by."""
    L = str(shape)
    if L not in _CYCLE:
        from ..cost.models import corrected_model
        from ..verify import verify
        from .leaves.transport import road
        b = road(L, 1)
        prog = b.programs["carry"]
        rep = verify(prog, b.machine.arch, corrected_model("qccdsim_jones"), check_metrics=False)
        by_id = {i.id: i for i in prog.instructions}
        out: dict = {}
        for c in rep.result.cycles:
            cls = by_id[c.instr_id].cls
            out[cls] = max(out.get(cls, 0.0), c.t1 - c.t0)
        _CYCLE[L] = out
    return _CYCLE[L]


@dataclass
class RoadRun:
    prog: object
    times: list                      # [instruction id, t0, t1] for every cycle, absolute
    loaded: list                     # when each ion left the near stub
    arrived: list                    # when each ion reached the far stub
    late: list = field(default_factory=list)
    failed: list = field(default_factory=list)      # basic rules the program breaks
    violations: list = field(default_factory=list)


def _busy(windows, t0: float, t1: float) -> float | None:
    """The end of the first window overlapping [t0, t1), or None."""
    for a, b in windows:
        if a < t1 - 1e-9 and t0 < b - 1e-9:
            return b
    return None


_VERIFIED: dict = {}


def compile_carry(length: int, deps: list, takes: list, *, near_busy=(), far_busy=(),
                  name: str = "carry", verify_rules: bool = True) -> RoadRun:
    """Compile one carry: `deps[j]` is when the near side put ion j on the near stub,
    `takes[j]` when the far side takes it off the far stub (absolute µs, in the bundle's
    order).  `near_busy` / `far_busy`: the windows in which the op on that side drives its
    stub."""
    from ..cost.models import corrected_model
    from ..verify import verify
    from .leaves.common import TrackedProgram
    from .leaves.transport import parse_shape
    shape = str(length)
    L = parse_shape(shape)[0]
    n = len(deps)
    m = conveyor_machine(shape, n)
    d = road_cycle_us(shape)
    d_in, d_shift, d_out = d.get("port_in", 5.0), d.get("conveyor", 5.0), d.get("port_out", 5.0)
    rail = [f"C{i}" for i in range(L)]
    ions = [f"i{j}" for j in range(n)]
    tp = TrackedProgram(m, name, {ion: "XW" for ion in ions})
    times, loaded, arrived = [], [None] * n, [None] * n
    nxt = 0                                   # the next ion to load
    t = min(deps) if deps else 0.0
    guard = 0
    while any(a is None for a in arrived):
        guard += 1
        if guard > 100000:
            raise RuntimeError(f"road of {L}: the conveyor never finished")
        at = {tp.pos[i]: i for i in ions if tp.pos[i] in rail}
        head = at.get(rail[-1])
        did = None
        # 1. deliver the head onto the far stub, unless the far side is driving it
        if head is not None:
            k = int(head[1:])
            if _busy(far_busy, t, t + d_out) is None:
                tp.move("port_out", [(head, "XE")], op="deliver")
                did = ("port_out", d_out)
                arrived[k] = t + d_out
        # 2. else load the next ion as soon as it is on the near stub and the near side is
        #    still: a load holds the rail for one cycle, a shift-first policy would hold the
        #    whole bundle behind the first ion until it left the road
        if did is None and nxt < n and deps[nxt] <= t + 1e-9 and rail[0] not in at:
            if _busy(near_busy, t, t + d_in) is None:
                tp.move("port_in", [(ions[nxt], rail[0])], op="load")
                did = ("port_in", d_in)
                loaded[nxt] = t
                nxt += 1
        # 3. else shift every ion that can advance, the blocked ones holding
        if did is None:
            moves, free = [], set(rail) - set(at)
            for i in range(L - 2, -1, -1):
                ion = at.get(rail[i])
                if ion is not None and rail[i + 1] in free:
                    moves.append((ion, rail[i + 1]))
                    free.discard(rail[i + 1])
                    free.add(rail[i])
            if moves:
                # one lab-frame direction a cycle: a bent road shifts each side of a corner
                # in a cycle of its own (R22), the leg nearer the far end first
                from .leaves.transport import split_shift
                groups = split_shift(m.arch.device, rail, {i: tp.pos[i] for i, _ in moves},
                                     list(reversed(moves)))
                for gi, group in enumerate(groups):
                    tp.move("conveyor", group, op="convey")
                    if gi + 1 < len(groups):
                        times.append([tp.prog.instructions[-1].id, round(t, 3), round(t + d_shift, 3)])
                        t += d_shift
                did = ("conveyor", d_shift)
        if did is None:
            # nothing can run now: wait for the next thing that frees something
            later = [x for x in ([deps[nxt]] if nxt < n else []) if x > t + 1e-9]
            later += [b for a, b in list(near_busy) + list(far_busy) if b > t + 1e-9 and a <= t + 1e-9]
            if not later:
                later = [b for a, b in list(near_busy) + list(far_busy) if b > t + 1e-9]
            if not later:
                raise RuntimeError(f"road of {L}: stuck at {t}")
            t = min(later)
            continue
        cls, dur = did
        times.append([tp.prog.instructions[-1].id, round(t, 3), round(t + dur, 3)])
        t += dur
    run = RoadRun(prog=tp.prog, times=times, loaded=loaded, arrived=arrived)
    for j, (a, k) in enumerate(zip(arrived, takes)):
        if a > k + 1e-6:
            run.late.append(j)
            run.violations.append(f"ion {j} reaches the far stub at {a:.1f} µs, after the far side "
                                  f"takes it at {k:.1f} µs")
    if not verify_rules:
        return run
    # the basic rules read the programme's cycles -- which ions move where, in which class --
    # not when they run (no rule here depends on elapsed time under this model), so a move
    # sequence is verified once however many carries share it
    shape = (L, n) + tuple((i.cls, tuple((q.ion, q.src, q.dst) for q in i.participants))
                           for i in tp.prog.instructions[1:])
    got = _VERIFIED.get(shape)
    if got is None:
        rep = verify(tp.prog, m.arch, corrected_model("qccdsim_jones"), check_metrics=False)
        got = (sorted(rep.rules.summary()["failed"]),
               [f"[{v.rule}] cycle {v.instr_id}: {v.message}" for v in rep.rules.violations[:5]])
        _VERIFIED[shape] = got
    run.failed = list(got[0])
    run.violations += got[1]
    if run.failed and not got[1]:
        run.violations.append(f"the road's program breaks {', '.join(run.failed)}")
    return run


def _door_windows(gir: dict, lib, leaves) -> tuple[dict, dict]:
    """(place, port) -> the windows in which the op there drives that door's stub: an ion
    leaving onto it (ends when the ion is on the stub) or taken off it (starts then)."""
    masters = {p: lib[v[0]] for p, v in gir["leaves"].items()}
    out_w: dict = {}
    in_w: dict = {}
    for e in gir["events"]:
        M = masters.get(e[3])
        O = M.ops.get(e[4]) if M is not None else None
        if O is None:
            continue
        for tr in O.transfers:
            dur = road_cycle_us(1).get("port_in", 5.0)
            for t in tr.times_us:
                if tr.dir == "out":
                    out_w.setdefault((e[3], tr.port), []).append((e[1] + t - dur, e[1] + t))
                elif tr.dir == "in":
                    in_w.setdefault((e[3], tr.port), []).append((e[1] + t, e[1] + t + dur))
    return out_w, in_w


def check_roads(gir: dict, lib, leaves) -> dict:
    """Every carry of `gir` compiled onto its road and judged (H1).  Carries of the same
    shape (road length, bundle size, departures and takes relative to the first departure,
    and the doors' busy windows likewise) compile to the same program: each shape is compiled
    and verified once."""
    out_w, in_w = _door_windows(gir, lib, leaves)
    nets = gir["nets"]
    violations, n_prog, n_shapes, failed_rules, bad, runs = [], 0, 0, set(), [], {}
    late = 0
    for c in gir["carries"]:
        net = nets.get(c[1])
        if net is None:
            continue
        L = net[5] if len(net) > 5 else net[4]
        t0 = c[4]
        deps = [t0 + x for x in c[5]]
        takes = [t0 + x for x in c[12]]
        src = (c[2], c[3])
        far = (net[2], net[3]) if (net[0], net[1]) == src else (net[0], net[1])
        lo, hi = min(deps) - 1.0, max(takes) + 1.0
        # every move the ops at the two doors make on their stubs while the bundle is on the
        # road: an ion's own handoff only touches its load (or delivery) end to end, so the
        # road is kept off the stub exactly while the op on that side drives it
        near = [w for w in out_w.get(src, []) if w[1] > lo and w[0] < hi]
        farb = [w for w in in_w.get(far, []) if w[1] > lo and w[0] < hi]
        key = (L, tuple(round(x - t0, 3) for x in deps), tuple(round(x - t0, 3) for x in takes),
               tuple((round(a - t0, 3), round(b - t0, 3)) for a, b in near),
               tuple((round(a - t0, 3), round(b - t0, 3)) for a, b in farb))
        n_prog += 1
        run = _RUNS.get(key)
        if run is None:
            n_shapes += 1
            run = compile_carry(L, [x - t0 for x in deps], [x - t0 for x in takes],
                                near_busy=[(a - t0, b - t0) for a, b in near],
                                far_busy=[(a - t0, b - t0) for a, b in farb],
                                name=f"chan{L}.carry")
            _RUNS[key] = run
        runs[c[0]] = run
        if run.violations:
            failed_rules.update(run.failed)
            late += len(run.late)
            bad.append(c[0])
            violations.append(f"carry {c[0]} on {c[1]} (instruction {c[9]}): " + "; ".join(run.violations[:2]))
    return {"violations": violations, "bad": bad, "runs": runs,
            "metrics": {"carries": n_prog, "programs": n_shapes, "late_deliveries": late,
                        "rules_broken": sorted(failed_rules)}}


# --------------------------------------------------------------------------- the page


def run_json(run: RoadRun) -> dict:
    """A compiled carry as the page replays it: each cycle `[t0, t1, class, [[ion, from, to],
    ...]]`, times from the carry's start, places as node indices along the road -- 0 the near
    stub, 1..L its sites, L + 1 the far stub."""
    def idx(node: str) -> int:
        if node == "XW":
            return 0
        if node.startswith("C"):
            return int(node[1:]) + 1
        return -1                                   # XE: the far end, filled in below
    ins = {i.id: i for i in run.prog.instructions}
    L = 0
    for i in run.prog.instructions[1:]:
        for q in i.participants:
            for nd in (q.src, q.dst):
                if nd.startswith("C"):
                    L = max(L, int(nd[1:]) + 1)
    cycles = []
    for iid, t0, t1 in run.times:
        i = ins[iid]
        moves = [[int(q.ion[1:]), idx(q.src), idx(q.dst)] for q in i.participants]
        for mv in moves:
            mv[1] = L + 1 if mv[1] < 0 else mv[1]
            mv[2] = L + 1 if mv[2] < 0 else mv[2]
        cycles.append([t0, t1, i.cls, moves])
    return {"n": len(run.arrived), "cycles": cycles}


def attach_runs(g: dict, lib, leaves) -> None:
    """Give a schedule the compiled program of every carry, for the page to replay: the
    root gets `road_programs` (each distinct program once) and every body (the root, and
    each macro of a folded run) `road_runs`, its carry ids -> an index there.  The very
    programs H1 judged (`check_roads`), on each body's own view."""
    programs: list = []
    index: dict = {}

    def take(body_gir, target):
        r = check_roads(body_gir, lib, leaves)
        # a view renumbers its carries by time; a road carries one bundle at a time (G13),
        # and an own view does not shift times, so (road, start) is the body's carry
        own = {(c[1], round(c[4], 3)): c[0] for c in target["carries"]}
        by_id = {c[0]: c for c in body_gir["carries"]}
        out = {}
        for cid, run in r["runs"].items():
            c = by_id[cid]
            k = id(run)
            if k not in index:
                index[k] = len(programs)
                programs.append(run_json(run))
            out[own.get((c[1], round(c[4], 3)), cid)] = index[k]
        target["road_runs"] = out

    if g.get("macros"):
        from .fold import view
        take(view(g, None), g)
        for mid in g["macros"]:
            take(view(g, mid), g["macros"][mid])
    else:
        take(g, g)
    g["road_programs"] = programs


# --------------------------------------------------------------------------- the machine's wiring


_REPORTS: dict = {}


def _report(key, arch):
    if key not in _REPORTS:
        from ..cost.hardware import hardware_report
        _REPORTS[key] = hardware_report(arch)
    return _REPORTS[key]


def machine_census(gir: dict, lib, leaves) -> dict:
    """The machine's electrodes, switches and DACs, counted the way one device's are
    (`qccd.cost.hardware.hardware_report`) for every device the floor holds: each place and
    junction as its master declares its control plane, and each road as its conveyor.  Each
    device drives its own plane (every leaf declares one); a design that shared a plane
    between devices would have to say so, and its devices would then have to play the same
    waveform in the same cycle."""
    from ..arch.device import Architecture
    tot = {"electrodes": 0, "switches": 0, "dacs": 0, "dacs_broadcast": 0, "dacs_compensation": 0}
    by_family: dict = {}

    def add(fam, r):
        row = by_family.setdefault(fam, {"devices": 0, "electrodes": 0, "dacs": 0})
        row["devices"] += 1
        row["electrodes"] += r.electrodes
        row["dacs"] += r.dacs
        for k in tot:
            tot[k] += getattr(r, k)

    for path, v in gir["leaves"].items():
        m = lib[v[0]]
        if m.family in ("decoder", "archive"):
            continue
        d = leaves.data.get(m.name)
        if not d or "arch" not in d:
            continue
        add(m.family if m.family != "road" else "junction", _report(("leaf", m.name), Architecture.from_json(d["arch"])))
    for net, v in gir["nets"].items():
        L = v[5] if len(v) > 5 else int(v[4])
        add("road", _report(("road", L), conveyor_machine(L, 9).arch))
        # (a bent road is its own conveyor: its corners are sites like any other)
    return dict(tot, by_family=by_family)
