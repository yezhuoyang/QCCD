"""Beat Cyclone under Cyclone's own cost model.

Cyclone (Khan et al., HPCA 2026, arXiv 2511.15910) runs a syndrome round on a ring of x
traps: one ancilla per trap, every ancilla does one X check and then one Z check, and all
ancillas move one trap clockwise per lockstep step.  A step costs

    g * max(1, P)  +  3 * g * A  +  s

where P is the largest number of CX any trap does in the step (the gates of one trap are
serial, g = max(100, 13.33 C - 54) us), 3gA is a gate swap charged for every ancilla every
step, and s = 165 us is split 80 + move 5 + merge 80.  One 100 us H layer opens the round and
one closes it.  A phase runs while any of its checks has a gate left (the artifact's
``while (checkEmpty(...) == False)``), so its length is set by the worst check: the number
of traps its ancilla must pass before it has met every data qubit of the check.

The artifact places data round-robin (its source: ``#NAIVE MAPPING - REPLACE WITH FIXED
PARTITIONER``) and gives check i to ancilla i, so some check always spans the whole ring and
each phase takes a full rotation: 2x steps.  Nothing in the model forces that.  This module
keeps every primitive, the step rule, the one-way rotation, the per-step swap and the
circuit, and changes only what the model leaves free:

    which trap each data qubit sits in        (data per trap <= C - A)
    which ancilla does which X and Z check    (each ancilla still does one of each)
    where each ancilla starts                 (one per trap, A = 1)

A `Plan` says those three things.  `write` turns a plan into a `TimedSchedule` with exactly
the event rules `importers.import_cyclone` uses for the artifact's own compiles, by running
the artifact's loop (gates of a step at t0 + j*g in their trap, then per ancilla a split that
carries the folded gate swap, a move across the degree-2 junction and a merge into the next
trap).  `verify` replays the saved schedule with `timed.check` under `models.CYCLONE` and
`models.cyclone_duration` against the circuit built from the TRUE check matrices, and runs a
noiseless stim memory experiment over the executed CX order.  `optimize` searches plans.

With one ancilla per trap (x = m/2) every trap starts exactly one X window and one Z window,
so a plan is equivalent to a *layout*: data traps, plus for each basis a permutation of the
checks onto start traps (`layout_of`, `plan_from_layout`).  Check r's ancilla meets data d
at step (trap(d) - start(r)) mod x, so a phase lasts max_r (1 + max_d that offset) steps and
does max_r (gates of r at offset j) layers at step j.  The searches:

``optimize``  simulated annealing in two stages.
    1. feasibility: for target phase lengths (kX, kZ), minimise the summed ring distance from
       every data qubit to the window [start, start + k - 1] of each of its checks; moves are
       data moves/swaps, swaps and block rotations of start traps, and shifts of a run of
       traps' contents.  Each time the penalty reaches zero the longer target drops by one.
    2. cost: with the windows as hard limits, minimise the exact bill 2*100 + sum over steps
       of (3g + 165 + g * max(1, P_j)), with a small tie-breaker on the number of checks
       needing a phase's last step and on the number doing a step's most gates.
    The seed matters more than the moves: `exact_starts` (bipartite matching: the shortest
    window that gives every check its own start trap) completes any data placement, and
    `hgp_layout` + `tanner_order` give HGP codes a row-major placement on the product of
    the two Tanner graphs, `bb_layout` gives BB codes one on their torus.
``sat_layout``  exact feasibility of (kX, kZ) as SAT (python-sat), with order-encoded data
    traps; warm-startable from a layout, with a wall-clock limit.  It proves a phase-length
    pair feasible (and produces the layout) or infeasible.
"""

from __future__ import annotations

import json
import math
import random
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .importers import cyclone_circuit, cyclone_device
from .models import CYCLONE, cyclone_duration
from .timed import Event, Report, TimedSchedule, check

__all__ = ["Plan", "gate_us", "ring_device", "naive_plan", "write", "circuit_for",
           "verify", "stim_check", "step_cost", "plan_cost", "optimize", "exact_starts",
           "sat_layout", "layout_of", "plan_from_layout", "tanner_order", "hgp_layout", "bb_layout",
           "hgp",
           "load_hgp_checks", "load_bb_checks", "artifact_plan"]

SPLIT_US = 80.0
MERGE_US = 80.0
MOVE_US = 5.0
H_US = 100.0


def gate_us(capacity: int) -> float:
    """The FM law the artifact keys to trap capacity: g = max(100, 13.33 C - 54)."""
    return max(100.0, 13.33 * capacity - 54)


def step_cost(capacity: int, p: int, a: int = 1) -> float:
    g = gate_us(capacity)
    return g * max(1, p) + 3 * g * a + SPLIT_US + MOVE_US + MERGE_US


# ------------------------------------------------------------------------------ the plan


@dataclass
class Plan:
    """Everything the Cyclone model leaves free, for one code on one ring.

    ``hx``/``hz``   the check supports the compiler EXECUTES (rows of data indices); for a
                    correct compile these are the code's true checks
    ``data_trap``   data qubit q -> trap
    ``anc_start``   ancilla k -> the trap it starts in (ancilla k is ion ``a{n+k}``)
    ``x_rows``      ancilla k -> the row of ``hx`` it measures; ``z_rows`` likewise
    ``data_order``  optional: trap -> data in the order the chain lists them
    """

    name: str
    n: int
    x: int
    capacity: int
    hx: list[list[int]]
    hz: list[list[int]]
    data_trap: list[int]
    anc_start: list[int]
    x_rows: list[int]
    z_rows: list[int]
    data_order: dict[int, list[int]] | None = None
    note: str = ""

    @property
    def m2(self) -> int:
        return len(self.anc_start)

    def to_json(self) -> dict:
        return {"name": self.name, "n": self.n, "x": self.x, "capacity": self.capacity,
                "hx": self.hx, "hz": self.hz, "data_trap": self.data_trap,
                "anc_start": self.anc_start, "x_rows": self.x_rows, "z_rows": self.z_rows,
                "data_order": ({str(k): v for k, v in self.data_order.items()}
                               if self.data_order else None),
                "note": self.note}

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> "Plan":
        order = d.get("data_order")
        return cls(name=d["name"], n=int(d["n"]), x=int(d["x"]), capacity=int(d["capacity"]),
                   hx=[list(r) for r in d["hx"]], hz=[list(r) for r in d["hz"]],
                   data_trap=list(d["data_trap"]), anc_start=list(d["anc_start"]),
                   x_rows=list(d["x_rows"]), z_rows=list(d["z_rows"]),
                   data_order=({int(k): list(v) for k, v in order.items()} if order else None),
                   note=d.get("note", ""))

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.write_text(json.dumps(self.to_json(), separators=(",", ":")), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path) -> "Plan":
        return cls.from_json(json.loads(Path(path).read_text(encoding="utf-8")))

    def validate(self) -> None:
        """The model's static rules: one ancilla per trap (A = 1), data per trap <= C - 1,
        every data qubit placed, every check row used once per basis."""
        if len(self.data_trap) != self.n:
            raise ValueError(f"{len(self.data_trap)} data placed, the code has {self.n}")
        if any(not 0 <= t < self.x for t in list(self.data_trap) + list(self.anc_start)):
            raise ValueError("a qubit is placed outside the ring")
        if len(set(self.anc_start)) != len(self.anc_start):
            raise ValueError("two ancillas start in one trap (A = 1 allows one)")
        load = Counter(self.data_trap)
        worst = max(load.values(), default=0)
        if worst > self.capacity - 1:
            raise ValueError(f"a trap holds {worst} data; capacity {self.capacity} leaves "
                             f"{self.capacity - 1} beside its ancilla")
        if sorted(self.x_rows) != list(range(len(self.hx))) or \
                sorted(self.z_rows) != list(range(len(self.hz))):
            raise ValueError("every X and every Z check must go to exactly one ancilla")
        if len(self.x_rows) != self.m2 or len(self.z_rows) != self.m2:
            raise ValueError("one X and one Z check per ancilla")


def ring_device(x: int, capacity: int):
    """`test_machines.make_circle_machine_length_n` as the run step dumps it, through
    `importers.cyclone_device`: segment q joins T_q's right end to J_q, segment x+q joins
    T_q's left end to J_(q-1)."""
    segs = []
    for q in range(x):
        segs.append({"id": q, "endpoints": [f"T{q}", f"J{q}"],
                     "trap_side": {"trap": q, "side": "R"}})
    for q in range(x):
        segs.append({"id": x + q, "endpoints": [f"T{q}", f"J{(q - 1) % x}"],
                     "trap_side": {"trap": q, "side": "L"}})
    raw = {"traps": [{"id": t, "capacity": capacity} for t in range(x)],
           "n_junctions": x, "segments": segs}
    return cyclone_device(raw)


def naive_plan(name: str, n: int, hx: list[list[int]], hz: list[list[int]], x: int,
               capacity: int, *, hz_executed: list[list[int]] | None = None) -> Plan:
    """The artifact's own plan (`cyclone_compiler.get_cyclone_mapping` and the check rule):
    data round-robin over traps in order of first appearance in cx_arr (X rows, then the Z
    rows the compiler built), ancilla i in trap i mod x, check i -> ancilla i mod (m/2).

    ``hz_executed`` lets the plan carry the Z rows the unpatched compiler builds (E5: every
    one a copy of the last X row), so its printed time can be reproduced too."""
    hz_run = hz_executed if hz_executed is not None else hz
    seen: list[int] = []
    mark = set()
    for row in list(hx) + list(hz_run):
        for d in row:
            if d not in mark:
                mark.add(d)
                seen.append(d)
    if len(seen) != n:
        raise ValueError(f"the checks touch {len(seen)} data, the code has {n}")
    data_trap = [0] * n
    order: dict[int, list[int]] = {t: [] for t in range(x)}
    for i, d in enumerate(seen):
        data_trap[d] = i % x
        order[i % x].append(d)
    m2 = len(hx)
    return Plan(name=name, n=n, x=x, capacity=capacity, hx=[list(r) for r in hx],
                hz=[list(r) for r in hz_run], data_trap=data_trap,
                anc_start=[k % x for k in range(m2)], x_rows=list(range(m2)),
                z_rows=list(range(m2)), data_order=order,
                note="the artifact's naive plan (round-robin data, check i -> ancilla i)")


# ---------------------------------------------------------------------------- the writer


def _phase_steps(plan: Plan, rows: list[list[int]], start: list[int]) -> list[list[list[int]]]:
    """Run one phase of the artifact's loop: per step, for each ancilla, the data of its
    check in its current trap that it has not met yet.  Returns steps[step][k] = data."""
    x = plan.x
    left = [set(rows[k]) for k in range(plan.m2)]
    order = [list(rows[k]) for k in range(plan.m2)]
    where = list(start)
    steps: list[list[list[int]]] = []
    while any(left):
        if len(steps) > x:
            raise RuntimeError("a phase did not finish in a full rotation")
        now = []
        for k in range(plan.m2):
            here = [d for d in order[k] if d in left[k] and plan.data_trap[d] == where[k]]
            left[k].difference_update(here)
            now.append(here)
        steps.append(now)
        where = [(t + 1) % x for t in where]
    return steps


def write(plan: Plan, *, name: str | None = None) -> TimedSchedule:
    """A plan -> timed primitives, with `import_cyclone`'s event rules.

    Phase X runs until every ancilla has met all of its X check, then phase Z starts from
    wherever the ancillas are and runs until every Z check is done.  Each step: gate layer j
    at t0 + j*g in each trap (CX(ancilla, data) in X, CX(data, ancilla) in Z); the step's
    gate time is g * max(1, P); then every ancilla splits (the 3gA gate swap folded into the
    split, `meta.swap_us`), moves across its degree-2 junction and merges into the next
    trap.  One 100 us H layer (an ion-less `gate1`) at either end.
    """
    plan.validate()
    x, n, A = plan.x, plan.n, 1
    g = gate_us(plan.capacity)
    swap = 3 * g * A
    dev = ring_device(x, plan.capacity)
    anc = [f"a{n + k}" for k in range(plan.m2)]
    events: list[Event] = []

    def add(**kw) -> None:
        events.append(Event(id=len(events), **kw))

    add(kind="gate1", t0=0.0, t1=H_US, ions=(), meta={"op": "H layer, charged once for all qubits"})
    t = H_US
    where = list(plan.anc_start)
    summary: dict[str, Any] = {"steps": {}, "sum_P": {}, "P": {}}
    for phase, rows_all, idx in (("X", plan.hx, plan.x_rows), ("Z", plan.hz, plan.z_rows)):
        rows = [rows_all[i] for i in idx]
        steps = _phase_steps(plan, rows, where)
        ps = []
        for si, now in enumerate(steps):
            p = max(1, max((len(h) for h in now), default=0))
            ps.append(p)
            for j in range(p):
                for tr in range(x):
                    for k in (kk for kk in range(plan.m2) if where[kk] == tr):
                        if j < len(now[k]):
                            d = f"d{now[k][j]}"
                            ions = (anc[k], d) if phase == "X" else (d, anc[k])
                            add(kind="gate", t0=t + j * g, t1=t + (j + 1) * g, ions=ions,
                                at=f"T{tr}", meta={"op": "CX", "phase": phase, "step": si})
            ts = t + p * g
            by_trap = sorted(range(plan.m2), key=lambda k: where[k])
            for k in by_trap:
                q = where[k]
                nxt = (q + 1) % x
                add(kind="split", t0=ts, t1=ts + swap + SPLIT_US, ions=(anc[k],), at=f"T{q}",
                    seg=f"S{q}", meta={"op": "gate swap + split", "swap_us": swap})
                add(kind="move", t0=ts + swap + SPLIT_US, t1=ts + swap + SPLIT_US + MOVE_US,
                    ions=(anc[k],), src=f"S{q}", dst=f"S{x + nxt}", via=f"J{q}")
                add(kind="merge", t0=ts + swap + SPLIT_US + MOVE_US,
                    t1=ts + swap + SPLIT_US + MOVE_US + MERGE_US, ions=(anc[k],),
                    at=f"T{nxt}", seg=f"S{x + nxt}")
            t = ts + swap + SPLIT_US + MOVE_US + MERGE_US
            where = [(q + 1) % x for q in where]
        summary["steps"][phase] = len(steps)
        summary["sum_P"][phase] = sum(ps)
        summary["P"][phase] = ps
    add(kind="gate1", t0=t, t1=t + H_US, ions=(), meta={"op": "ending H layer"})
    chains: dict[str, list[str]] = {f"T{q}": [] for q in range(x)}
    if plan.data_order:
        for q, ds in plan.data_order.items():
            chains[f"T{q}"] = [f"d{d}" for d in ds]
        if sorted(int(c[1:]) for v in chains.values() for c in v) != list(range(n)):
            raise ValueError("data_order does not list every data qubit once")
        for d in range(n):
            if f"d{d}" not in chains[f"T{plan.data_trap[d]}"]:
                raise ValueError(f"data_order puts d{d} outside its trap")
    else:
        for d in range(n):
            chains[f"T{plan.data_trap[d]}"].append(f"d{d}")
    for k in range(plan.m2):
        chains[f"T{plan.anc_start[k]}"].append(anc[k])
    total = t + H_US
    claims = {"time_us": total, "steps_X": summary["steps"]["X"],
              "steps_Z": summary["steps"]["Z"], "sum_P_X": summary["sum_P"]["X"],
              "sum_P_Z": summary["sum_P"]["Z"], "P_X": summary["P"]["X"],
              "P_Z": summary["P"]["Z"], "g_us": g, "swap_us": swap, "A": A}
    return TimedSchedule(name=name or plan.name, device=dev, chains=chains, events=events,
                         source={"tool": "qccd.repro.opt_cyclone.write", "plan": plan.name,
                                 "note": plan.note, "x": x, "capacity": plan.capacity},
                         claims=claims)


def plan_cost(plan: Plan) -> dict:
    """The bill the writer would produce, without writing events."""
    where = list(plan.anc_start)
    out: dict[str, Any] = {}
    total = 2 * H_US
    for phase, rows_all, idx in (("X", plan.hx, plan.x_rows), ("Z", plan.hz, plan.z_rows)):
        steps = _phase_steps(plan, [rows_all[i] for i in idx], where)
        ps = [max(1, max((len(h) for h in now), default=0)) for now in steps]
        total += sum(step_cost(plan.capacity, p) for p in ps)
        out[f"steps_{phase}"] = len(steps)
        out[f"sum_P_{phase}"] = sum(ps)
        where = [(q + len(steps)) % plan.x for q in where]
    out["time_us"] = total
    return out


# ---------------------------------------------------------------------------- the proof


def circuit_for(plan: Plan, hx_true: list[list[int]], hz_true: list[list[int]]):
    """`importers.cyclone_circuit` over the TRUE check matrices, with each row on the ancilla
    the plan gives it (the rows are only relabelled, never changed)."""
    ancillas = {str(plan.n + k): None for k in range(plan.m2)}
    hx = [list(hx_true[plan.x_rows[k]]) for k in range(plan.m2)]
    hz = [list(hz_true[plan.z_rows[k]]) for k in range(plan.m2)]
    return cyclone_circuit({"X": ancillas}, hx, hz)


def stim_check(sched: TimedSchedule, plan: Plan, hx_true: list[list[int]],
               hz_true: list[list[int]], *, shots: int = 32) -> dict:
    """Does the round, as executed, measure every stabilizer?

    The circuit is the schedule's own CX order (gate events by start time): R and H on the
    ancillas, the X-phase CX(ancilla, data), H, measure; reset; the Z-phase CX(data,
    ancilla), measure.  Two noiseless memory experiments run two rounds each, one from
    |0...0> and one from |+...+>, and then measure the data in that basis.  Detectors: every
    check of the prepared basis in round 1 alone, every check's round-2 outcome against
    round 1, and every check of the prepared basis against the parity of its data in the
    final measurement -- that last one ties each outcome to the TRUE row the plan assigns.
    Every detector must be deterministic (stim's detector error model refuses otherwise) and
    every sampled detector must read 0.
    """
    import stim

    n, m2 = plan.n, plan.m2
    anc_index = {f"a{n + k}": n + k for k in range(m2)}
    gates = sorted((e for e in sched.events if e.kind == "gate"), key=lambda e: (e.t0, e.id))
    seq = {"X": [], "Z": []}
    for e in gates:
        ph = e.meta.get("phase")
        if ph == "X":
            a, d = e.ions
        else:
            d, a = e.ions
        seq[ph].append((anc_index[a], int(d[1:])))
    # the circuit below runs every X-phase CX before every Z-phase CX: say whether the
    # schedule did too (the checker's circuit test already holds each data qubit to it)
    x_end = max((e.t1 for e in gates if e.meta.get("phase") == "X"), default=0.0)
    z_begin = min((e.t0 for e in gates if e.meta.get("phase") == "Z"), default=math.inf)
    xrow = {n + k: hx_true[plan.x_rows[k]] for k in range(m2)}
    zrow = {n + k: hz_true[plan.z_rows[k]] for k in range(m2)}
    ancs = list(range(n, n + m2))
    data = list(range(n))
    out = {}
    for basis in ("Z", "X"):
        c = stim.Circuit()
        c.append("R" if basis == "Z" else "RX", data)
        nmeas = 0
        rec: dict[tuple[int, str, int], int] = {}
        for rnd in (0, 1):
            c.append("R", ancs)
            c.append("H", ancs)
            for a, d in seq["X"]:
                c.append("CX", [a, d])
            c.append("H", ancs)
            c.append("M", ancs)
            for i, a in enumerate(ancs):
                rec[(rnd, "X", a)] = nmeas + i
            nmeas += m2
            c.append("R", ancs)
            for a, d in seq["Z"]:
                c.append("CX", [d, a])
            c.append("M", ancs)
            for i, a in enumerate(ancs):
                rec[(rnd, "Z", a)] = nmeas + i
            nmeas += m2
        c.append("M" if basis == "Z" else "MX", data)
        final = {d: nmeas + i for i, d in enumerate(data)}
        nmeas += n

        def target(i: int):
            return stim.target_rec(i - nmeas)

        dets = 0
        rows = zrow if basis == "Z" else xrow
        for a in ancs:
            c.append("DETECTOR", [target(rec[(0, basis, a)])])
            dets += 1
        for b in ("X", "Z"):
            for a in ancs:
                c.append("DETECTOR", [target(rec[(1, b, a)]), target(rec[(0, b, a)])])
                dets += 1
        for a in ancs:
            c.append("DETECTOR", [target(rec[(1, basis, a)])] + [target(final[d]) for d in rows[a]])
            dets += 1
        try:
            c.detector_error_model()
            deterministic = True
            why = ""
        except ValueError as exc:            # stim: "non-deterministic detectors"
            deterministic = False
            why = str(exc).splitlines()[0][:200]
        fired = 0
        if deterministic:
            sample = c.compile_detector_sampler().sample(shots)
            fired = int(sample.sum())
        out[basis] = {"detectors": dets, "deterministic": deterministic, "fired": fired,
                      "why": why, "cx": len(seq["X"]) + len(seq["Z"])}
    out["x_before_z"] = x_end <= z_begin + 1e-9
    out["ok"] = out["x_before_z"] and all(out[b]["deterministic"] and out[b]["fired"] == 0
                                          for b in ("Z", "X"))
    return out


def verify(sched: TimedSchedule, plan: Plan, hx_true: list[list[int]],
           hz_true: list[list[int]], *, stim_shots: int = 32) -> dict:
    """`timed.check` under Cyclone's profile and duration law against the true circuit, plus
    the stim memory check.  ``ok`` only when both are clean."""
    rep: Report = check(sched, CYCLONE, duration=cyclone_duration,
                        circuit=circuit_for(plan, hx_true, hz_true))
    st = stim_check(sched, plan, hx_true, hz_true, shots=stim_shots)
    return {"report": rep, "check_ok": rep.ok, "stim": st, "ok": rep.ok and st["ok"],
            "makespan_us": rep.metrics["makespan_us"]}


# --------------------------------------------------------------------------- the codes


def hgp(h1: Sequence[Sequence[int]], h2: Sequence[Sequence[int]]
        ) -> tuple[list[list[int]], list[list[int]]]:
    """QUITS's `HgpCode(h1, h2)` check rows, as the artifact reads them:
    hx = [I_n1 (x) h2 | h1^T (x) I_m2], hz = [h1 (x) I_n2 | I_m1 (x) h2^T]."""
    m1, n1 = len(h1), len(h1[0])
    m2, n2 = len(h2), len(h2[0])
    hx: list[list[int]] = []
    for i in range(n1):                      # X check (i, a): left (i, j) j in N(a), right (r, a) r in N(i)
        for a in range(m2):
            row = [i * n2 + j for j in range(n2) if h2[a][j]]
            row += [n1 * n2 + r * m2 + a for r in range(m1) if h1[r][i]]
            hx.append(sorted(row))
    hz: list[list[int]] = []
    for r in range(m1):                      # Z check (r, j): left (i, j) i in N(r), right (r, a) a in N(j)
        for j in range(n2):
            row = [i * n2 + j for i in range(n1) if h1[r][i]]
            row += [n1 * n2 + r * m2 + a for a in range(m2) if h2[a][j]]
            hz.append(sorted(row))
    return hx, hz


def load_hgp_checks(run_dir: str | Path) -> tuple[int, list[list[int]], list[list[int]]]:
    """``e5_check_lists.json`` of an HGP run: (n, hx_rows_true, hz_rows_true)."""
    e = json.loads((Path(run_dir) / "e5_check_lists.json").read_text(encoding="utf-8"))
    return int(e["n"]), [list(r) for r in e["hx_rows_true"]], [list(r) for r in e["hz_rows_true"]]


def load_bb_checks(run_dir: str | Path) -> tuple[int, list[list[int]], list[list[int]]]:
    """``input_tuple.json`` of a BB run (IBM's single-cycle circuit): the non-empty x_arr rows
    are the X checks, the non-empty z_arr rows the Z checks, in the compiler's order."""
    t = json.loads((Path(run_dir) / "input_tuple.json").read_text(encoding="utf-8"))
    hx = [list(r) for r in t["x_arr"] if r]
    hz = [list(r) for r in t["z_arr"] if r]
    n = 1 + max(max(r) for r in hx + hz)
    return n, hx, hz


def artifact_plan(schedule_json: str | Path, hx_exec: list[list[int]],
                  hz_exec: list[list[int]]) -> Plan:
    """The plan a dumped Cyclone compile actually ran: its initial layout, its ancillas'
    start traps and its check assignment (matched to rows of the executed matrices)."""
    s = json.loads(Path(schedule_json).read_text(encoding="utf-8"))
    cfg = s["config"]
    n = int(cfg["n"])
    x = int(s["device"]["n_traps"])
    cap = int(s["device"]["traps"][0]["capacity"])
    data_trap = [-1] * n
    order: dict[int, list[int]] = {}
    anc_trap: dict[int, int] = {}
    for trap, ions in s["initial_layout"].items():
        q = int(trap)
        order[q] = []
        for ion in ions:
            if ion.startswith("d"):
                data_trap[int(ion[1:])] = q
                order[q].append(int(ion[1:]))
            else:
                anc_trap[int(ion[1:])] = q
    ancs = sorted(anc_trap)

    def rows_of(phase: str, rows: list[list[int]]) -> list[int]:
        index: dict[tuple[int, ...], list[int]] = {}
        for i, r in enumerate(rows):
            index.setdefault(tuple(sorted(r)), []).append(i)
        out = []
        for a in ancs:
            sup = s["check_to_ancilla"][phase][str(a)][0]
            out.append(index[tuple(sorted(sup))].pop(0))
        return out

    return Plan(name=s.get("config_name", Path(schedule_json).parent.name), n=n, x=x,
                capacity=cap, hx=[list(r) for r in hx_exec], hz=[list(r) for r in hz_exec],
                data_trap=data_trap, anc_start=[anc_trap[a] for a in ancs],
                x_rows=rows_of("X", hx_exec), z_rows=rows_of("Z", hz_exec), data_order=order,
                note="read from the artifact's schedule.json")


# ------------------------------------------------------------------------ the optimizer


class _Search:
    """State and incremental costs for the annealer (one ancilla per trap: m/2 == x)."""

    def __init__(self, n: int, hx: list[list[int]], hz: list[list[int]], x: int, cap: int,
                 rng: random.Random, capacity: int | None = None):
        self.n, self.x, self.cap, self.rng = n, x, cap, rng
        self.g = gate_us(cap + 1 if capacity is None else capacity)
        self.step_fixed = 3 * self.g + SPLIT_US + MOVE_US + MERGE_US
        self.rows = (hx, hz)
        self.of_data = ([[] for _ in range(n)], [[] for _ in range(n)])
        for ph in (0, 1):
            for r, row in enumerate(self.rows[ph]):
                for d in row:
                    self.of_data[ph][d].append(r)
        self.pos = [0] * n
        self.members: list[list[int]] = [[] for _ in range(x)]
        self.start = ([0] * len(hx), [0] * len(hz))      # row -> start trap
        self.at = ([0] * x, [0] * x)                     # start trap -> row
        self.k = [x, x]

    # -- state ----------------------------------------------------------------------
    def set_layout(self, pos: list[int], sx: list[int], sz: list[int]) -> None:
        self.pos = list(pos)
        self.members = [[] for _ in range(self.x)]
        for d, t in enumerate(self.pos):
            self.members[t].append(d)
        self.start = (list(sx), list(sz))
        self.at = ([0] * self.x, [0] * self.x)
        for ph in (0, 1):
            for r, s in enumerate(self.start[ph]):
                self.at[ph][s] = r

    def snapshot(self) -> tuple:
        return (list(self.pos), list(self.start[0]), list(self.start[1]))

    # -- window penalty -----------------------------------------------------------------
    def row_pen(self, ph: int, r: int, s: int | None = None) -> int:
        s = self.start[ph][r] if s is None else s
        k, pos, x = self.k[ph], self.pos, self.x
        tot = 0
        for d in self.rows[ph][r]:
            o = (pos[d] - s) % x
            if o >= k:
                tot += min(x - o, o - k + 1)
        return tot

    def penalty(self) -> int:
        return sum(self.row_pen(ph, r) for ph in (0, 1) for r in range(len(self.rows[ph])))

    def reach(self, ph: int, r: int, s: int | None = None) -> int:
        s = self.start[ph][r] if s is None else s
        return 1 + max((self.pos[d] - s) % self.x for d in self.rows[ph][r])

    def lengths(self) -> tuple[int, int]:
        return tuple(max(self.reach(ph, r) for r in range(len(self.rows[ph]))) for ph in (0, 1))

    # -- stage 1: drive the window penalty to zero for targets k ---------------------------
    def _permute_traps(self, to: Mapping[int, int]) -> None:
        """Move the whole contents of trap t (its data and the checks starting there) to
        trap to[t]; ``to`` permutes a set of traps."""
        pos, members, start, at = self.pos, self.members, self.start, self.at
        moved = {t: (members[t], at[0][t], at[1][t]) for t in to}
        for t, (ds, r0, r1) in moved.items():
            b = to[t]
            members[b] = ds
            for d in ds:
                pos[d] = b
            at[0][b], at[1][b] = r0, r1
            start[0][r0], start[1][r1] = b, b

    def anneal_feasible(self, moves: int, t0: float = 1.5, t1: float = 0.05,
                        block: float = 0.25, segment: float = 0.0) -> int:
        rng, x, cap = self.rng, self.x, self.cap
        pos, members, start, at, of_data, rows = (self.pos, self.members, self.start, self.at,
                                                  self.of_data, self.rows)
        k = self.k
        n = self.n
        pen = self.penalty()
        if pen == 0:
            return 0
        exp, rand = math.exp, rng.random
        ln = math.log(t1 / t0)

        def dterm(d: int, p: int) -> int:
            tot = 0
            for ph in (0, 1):
                kk = k[ph]
                st = start[ph]
                for r in of_data[ph][d]:
                    o = (p - st[r]) % x
                    if o >= kk:
                        tot += min(x - o, o - kk + 1)
            return tot

        for it in range(moves):
            temp = t0 * exp(ln * it / moves)
            u = rand()
            if segment and rand() < segment:              # shift a run of traps' contents
                length = rng.randint(2, 10)
                a = rng.randrange(x)
                step = 1 if rand() < 0.5 else -1
                traps = [(a + i) % x for i in range(length)]
                to = {t: traps[(i + step) % length] for i, t in enumerate(traps)}
                rs = (set(), set())
                for t in traps:
                    for d in members[t]:
                        rs[0].update(of_data[0][d])
                        rs[1].update(of_data[1][d])
                    rs[0].add(at[0][t])
                    rs[1].add(at[1][t])
                old = sum(self.row_pen(ph, r) for ph in (0, 1) for r in rs[ph])
                self._permute_traps(to)
                delta = sum(self.row_pen(ph, r) for ph in (0, 1) for r in rs[ph]) - old
                if delta <= 0 or rand() < exp(-delta / temp):
                    pen += delta
                else:
                    self._permute_traps({b: a_ for a_, b in to.items()})
                if pen == 0:
                    return 0
                continue
            if u < 0.6:                                   # data move or swap
                d = rng.randrange(n)
                pd = pos[d]
                if rand() < 0.7:
                    t = (pd + rng.randint(-4, 4)) % x
                else:
                    t = rng.randrange(x)
                if t == pd:
                    continue
                if len(members[t]) < cap and rand() < 0.5:
                    delta = dterm(d, t) - dterm(d, pd)
                    if delta <= 0 or rand() < exp(-delta / temp):
                        members[pd].remove(d)
                        members[t].append(d)
                        pos[d] = t
                        pen += delta
                else:
                    if not members[t]:
                        continue
                    e = members[t][rng.randrange(len(members[t]))]
                    # shared checks see both move; evaluate exactly by applying
                    before = dterm(d, pd) + dterm(e, t)
                    pos[d], pos[e] = t, pd
                    after = dterm(d, t) + dterm(e, pd)
                    delta = after - before
                    if delta <= 0 or rand() < exp(-delta / temp):
                        members[pd].remove(d)
                        members[t].remove(e)
                        members[t].append(d)
                        members[pd].append(e)
                        pen += delta
                    else:
                        pos[d], pos[e] = pd, t
            elif rand() < block:                          # rotate the checks of a run of starts
                ph = 0 if u < 0.8 else 1
                length = rng.randint(3, 16)
                s0 = rng.randrange(x)
                step = 1 if rand() < 0.5 else -1
                traps = [(s0 + i) % x for i in range(length)]
                rs = [at[ph][t] for t in traps]
                new = traps[step:] + traps[:step]         # row at traps[i] -> new[i]
                delta = 0
                for r, a, b in zip(rs, traps, new):
                    delta += self.row_pen(ph, r, b) - self.row_pen(ph, r, a)
                if delta <= 0 or rand() < exp(-delta / temp):
                    for r, b in zip(rs, new):
                        start[ph][r] = b
                        at[ph][b] = r
                    pen += delta
            else:                                         # swap two checks' start traps
                ph = 0 if u < 0.8 else 1
                nr = len(rows[ph])
                r1 = rng.randrange(nr)
                s1 = start[ph][r1]
                s2 = (s1 + rng.randint(-6, 6)) % x if rand() < 0.8 else rng.randrange(x)
                if s2 == s1:
                    continue
                r2 = at[ph][s2]
                delta = (self.row_pen(ph, r1, s2) + self.row_pen(ph, r2, s1)
                         - self.row_pen(ph, r1, s1) - self.row_pen(ph, r2, s2))
                if delta <= 0 or rand() < exp(-delta / temp):
                    start[ph][r1], start[ph][r2] = s2, s1
                    at[ph][s1], at[ph][s2] = r2, r1
                    pen += delta
            if pen == 0:
                return 0
        return pen

    # -- stage 2: the exact bill, windows as hard limits -------------------------------
    def _gates(self, ph: int, r: int) -> tuple[dict[int, int], int]:
        """Row r's gates per step of its phase ({offset: count}) and its reach."""
        s, x, pos = self.start[ph][r], self.x, self.pos
        g: dict[int, int] = {}
        for d in self.rows[ph][r]:
            o = (pos[d] - s) % x
            g[o] = g.get(o, 0) + 1
        return g, 1 + max(g)

    def _bill_init(self) -> None:
        x = self.x
        self.gmap = ([], [])
        self.rch = ([], [])
        self.cnt = ([[0] * (self.cap + 2) for _ in range(x)], [[0] * (self.cap + 2) for _ in range(x)])
        self.hist = ([0] * (x + 1), [0] * (x + 1))
        for ph in (0, 1):
            for r in range(len(self.rows[ph])):
                g, re = self._gates(ph, r)
                self.gmap[ph].append(g)
                self.rch[ph].append(re)
                self.hist[ph][re] += 1
                for j, c in g.items():
                    self.cnt[ph][j][c] += 1
        self.P = ([self._top(0, j) for j in range(x)], [self._top(1, j) for j in range(x)])
        self.klen = [max(self.rch[0]), max(self.rch[1])]

    def _top(self, ph: int, j: int) -> int:
        c = self.cnt[ph][j]
        for v in range(len(c) - 1, 0, -1):
            if c[v]:
                return v
        return 0

    def bill(self) -> float:
        """465 per step (3g + 165 at g = 100) plus g per gate layer, over both phases."""
        tot = 2 * H_US
        for ph in (0, 1):
            k = self.klen[ph]
            tot += k * self.step_fixed + self.g * sum(max(1, p) for p in self.P[ph][:k])
        return tot

    def _rebuild(self, ph: int, r: int) -> tuple[set, bool]:
        """Recompute row r's gates and reach after a move; returns (touched offsets, ok)."""
        old = self.gmap[ph][r]
        cnt = self.cnt[ph]
        for j, c in old.items():
            cnt[j][c] -= 1
        g, re = self._gates(ph, r)
        for j, c in g.items():
            cnt[j][c] += 1
        self.gmap[ph][r] = g
        h = self.hist[ph]
        h[self.rch[ph][r]] -= 1
        h[re] += 1
        self.rch[ph][r] = re
        return set(old) | set(g), re <= self.k[ph]

    def anneal_bill(self, moves: int, temp0: float = 150.0, temp1: float = 5.0,
                    tie: float = 2.0) -> float:
        """Anneal the exact bill.  Windows (``self.k``) stay hard limits; a small tie-breaker
        (``tie`` us per check that needs the last step of its phase, and per check doing
        the most gates of a step) gives the plateaus a slope."""
        self._bill_init()
        rng, x, cap, n = self.rng, self.x, self.cap, self.n
        pos, members, start, at, of_data = (self.pos, self.members, self.start, self.at,
                                            self.of_data)
        exp, rand = math.exp, rng.random
        ln = math.log(temp1 / temp0)

        def score() -> float:
            tot = self.bill()
            for ph in (0, 1):
                tot += tie * self.hist[ph][self.klen[ph]]
                for j in range(self.klen[ph]):
                    p = self.P[ph][j]
                    if p > 1:
                        tot += tie * self.cnt[ph][j][p]
            return tot

        cur = score()
        best = (cur, self.snapshot())
        for it in range(moves):
            temp = temp0 * exp(ln * it / moves)
            u = rand()
            undo: list = []
            if u < 0.6:
                d = rng.randrange(n)
                pd = pos[d]
                t = (pd + rng.randint(-3, 3)) % x if rand() < 0.8 else rng.randrange(x)
                if t == pd:
                    continue
                e = -1
                if not (len(members[t]) < cap and rand() < 0.5):
                    if not members[t]:
                        continue
                    e = members[t][rng.randrange(len(members[t]))]
                pos[d] = t
                if e >= 0:
                    pos[e] = pd
                undo.append(("data", d, e, pd, t))
                rows = [(ph, r) for ph in (0, 1) for r in
                        set(of_data[ph][d]) | (set(of_data[ph][e]) if e >= 0 else set())]
            else:
                ph = 0 if u < 0.8 else 1
                if rand() < 0.3:
                    length = rng.randint(3, 12)
                    s0 = rng.randrange(x)
                    step = 1 if rand() < 0.5 else -1
                    traps = [(s0 + i) % x for i in range(length)]
                    new = traps[step:] + traps[:step]
                else:
                    s1 = rng.randrange(x)
                    s2 = (s1 + rng.randint(-5, 5)) % x if rand() < 0.85 else rng.randrange(x)
                    if s1 == s2:
                        continue
                    traps, new = [s1, s2], [s2, s1]
                rs = [at[ph][t] for t in traps]
                for r, b in zip(rs, new):
                    start[ph][r] = b
                    at[ph][b] = r
                undo.append(("start", ph, rs, traps))
                rows = [(ph, r) for r in rs]
            ok = True
            touched: list[tuple[int, int]] = []
            for ph, r in rows:
                js, good = self._rebuild(ph, r)
                ok = ok and good
                touched.extend((ph, j) for j in js)
            old_P = {}
            old_k = list(self.klen)
            for ph, j in touched:
                if (ph, j) not in old_P:
                    old_P[(ph, j)] = self.P[ph][j]
                    self.P[ph][j] = self._top(ph, j)
            for ph in (0, 1):
                k = self.k[ph]
                while k > 1 and not self.hist[ph][k]:
                    k -= 1
                self.klen[ph] = k
            new = score() if ok else math.inf
            delta = new - cur
            if ok and (delta <= 0 or rand() < exp(-delta / temp)):
                cur = new
                if cur < best[0] - 1e-9:
                    best = (cur, self.snapshot())
                if u < 0.6:
                    kind, d, e, pd, t = undo[0]
                    members[pd].remove(d)
                    members[t].append(d)
                    if e >= 0:
                        members[t].remove(e)
                        members[pd].append(e)
                continue
            # roll back
            kind = undo[0][0]
            if kind == "data":
                _, d, e, pd, t = undo[0]
                pos[d] = pd
                if e >= 0:
                    pos[e] = t
            else:
                _, ph0, rs, traps = undo[0]
                for r, a in zip(rs, traps):
                    start[ph0][r] = a
                    at[ph0][a] = r
            for ph, r in rows:
                self._rebuild(ph, r)
            for (ph, j), p in old_P.items():
                self.P[ph][j] = p
            self.klen = old_k
        self.set_layout(*best[1])
        self._bill_init()
        return self.bill()


def _spectral_order(n: int, rows: Iterable[list[int]], rng: random.Random) -> list[int]:
    """Data in the order of the angle of their two lowest non-trivial Laplacian
    eigenvectors: a cheap circular embedding of the check hypergraph."""
    import numpy as np

    lap = np.zeros((n, n))
    for row in rows:
        w = 1.0 / (len(row) - 1)
        for i in row:
            for j in row:
                if i != j:
                    lap[i, j] -= w
                    lap[i, i] += w
    _, vecs = np.linalg.eigh(lap)
    ang = np.arctan2(vecs[:, 2], vecs[:, 1])
    return [int(i) for i in np.argsort(ang)]


def tanner_order(h: Sequence[Sequence[int]], *, seed: int = 0, iters: int = 30000) -> list[int]:
    """A cyclic order of the Tanner graph of ``h`` (bits 0..n-1, then checks n..n+m-1) that
    keeps every closed neighbourhood on a short arc (annealed: the worst arc first, then the
    sum of fourth powers)."""
    m, nb = len(h), len(h[0])
    N = nb + m
    adj = [set() for _ in range(N)]
    for a in range(m):
        for j in range(nb):
            if h[a][j]:
                adj[j].add(nb + a)
                adj[nb + a].add(j)
    hoods = [sorted(adj[u] | {u}) for u in range(N)]

    def score(order: list[int]) -> float:
        where = [0] * N
        for i, u in enumerate(order):
            where[u] = i
        worst, tot = 0, 0
        for hood in hoods:
            ps = sorted(where[v] for v in hood)
            gap = max((ps[(i + 1) % len(ps)] - ps[i]) % N or N for i in range(len(ps)))
            s = N - gap + 1
            worst = max(worst, s)
            tot += s ** 4
        return worst * 1e4 + tot / 1e3

    rng = random.Random(seed)
    order = list(range(N))
    rng.shuffle(order)
    cur = score(order)
    best, best_order = cur, list(order)
    for it in range(iters):
        temp = 2.0 * (0.005) ** (it / iters)
        i, j = rng.randrange(N), rng.randrange(N)
        order[i], order[j] = order[j], order[i]
        new = score(order)
        if new <= cur or rng.random() < math.exp(-(new - cur) / temp):
            cur = new
            if cur < best:
                best, best_order = cur, list(order)
        else:
            order[i], order[j] = order[j], order[i]
    return best_order


def hgp_layout(h1: Sequence[Sequence[int]], h2: Sequence[Sequence[int]], x: int,
               rows: Sequence[int], cols: Sequence[int], *, transpose: bool = False) -> list[int]:
    """Row-major placement of `hgp(h1, h2)`'s data on a ring of x traps.  The data are the
    bit x bit and check x check nodes of the product of the two Tanner graphs; ``rows`` orders
    the nodes of h1's graph, ``cols`` those of h2's (as `tanner_order` numbers them).
    ``transpose`` makes h2's graph the major (slow) coordinate instead."""
    m1, n1 = len(h1), len(h1[0])
    m2, n2 = len(h2), len(h2[0])
    r1 = {u: i for i, u in enumerate(rows)}
    r2 = {v: i for i, v in enumerate(cols)}
    n = n1 * n2 + m1 * m2

    def grid(d: int) -> tuple[int, int]:
        if d < n1 * n2:
            return d // n2, d % n2
        e = d - n1 * n2
        return n1 + e // m2, n2 + e % m2

    if transpose:
        order = sorted(range(n), key=lambda d: (r2[grid(d)[1]], r1[grid(d)[0]]))
    else:
        order = sorted(range(n), key=lambda d: (r1[grid(d)[0]], r2[grid(d)[1]]))
    pos = [0] * n
    for i, d in enumerate(order):
        pos[d] = (i * x) // n
    return pos


def bb_layout(l: int, m: int, x: int, *, a: int = 1, b: int = 1, di: int = 0, dj: int = 0,
              i_major: bool = True) -> list[int]:
    """Row-major placement of a bivariate-bicycle code's data on a ring of x traps, in the
    labelling of IBM's circuit (as the artifact reads it): L(i, j) = i*m + j and
    R(i, j) = l*m + i*m + j on the torus Z_l x Z_m.  Coordinates are scaled by units a (mod
    l) and b (mod m); R(i, j) sits beside L(i - di, j - dj); ``i_major`` makes i the slow
    coordinate."""
    half = l * m
    key: dict[int, tuple] = {}
    for i in range(l):
        for j in range(m):
            for side, (ii, jj) in ((0, (i, j)), (1, ((i + di) % l, (j + dj) % m))):
                ci, cj = (a * ii) % l, (b * jj) % m
                key[side * half + i * m + j] = ((ci, cj) if i_major else (cj, ci)) + (side,)
    order = sorted(key, key=key.__getitem__)
    pos = [0] * (2 * half)
    for r, d in enumerate(order):
        pos[d] = (r * x) // (2 * half)
    return pos


def _span(pos: Sequence[int], row: Sequence[int], x: int) -> int:
    ps = sorted({pos[d] for d in row})
    if len(ps) <= 1:
        return len(ps)
    return x - max((ps[(i + 1) % len(ps)] - ps[i]) % x for i in range(len(ps))) + 1


def exact_starts(pos: Sequence[int], rows: Sequence[Sequence[int]], x: int,
                 k_min: int | None = None) -> tuple[int, list[int]]:
    """The shortest phase a placement allows: the least k for which every check gets its own
    start trap whose k-window holds all its data (Kuhn's augmenting paths; m/2 == x)."""
    k = max(_span(pos, r, x) for r in rows) if k_min is None else k_min
    while k <= x:
        cands = []
        for r in rows:
            ps = [pos[d] for d in r]
            cands.append([s for s in range(x) if max((p - s) % x for p in ps) < k])
        owner = [-1] * x

        def aug(r: int, seen: list[bool]) -> bool:
            stack = [(r, iter(cands[r]))]
            path: list[tuple[int, int]] = []
            while stack:
                row, it = stack[-1]
                for s in it:
                    if not seen[s]:
                        seen[s] = True
                        if owner[s] < 0:
                            path.append((row, s))
                            for rr, ss in path:          # flip the augmenting path
                                owner[ss] = rr
                            return True
                        path.append((row, s))
                        stack.append((owner[s], iter(cands[owner[s]])))
                        break
                else:
                    stack.pop()
                    if path:
                        path.pop()
            return False

        ok = True
        for r in sorted(range(len(rows)), key=lambda r: len(cands[r])):
            if not aug(r, [False] * x):
                ok = False
                break
        if ok:
            st = [0] * len(rows)
            for s, r in enumerate(owner):
                if r >= 0:
                    st[r] = s
            return k, st
        k += 1
    raise RuntimeError("no start assignment fits even a full rotation")


def optimize(name: str, n: int, hx: list[list[int]], hz: list[list[int]], *, x: int | None = None,
             capacity: int = 5, seed: int = 1, budget_s: float = 120.0, moves: int = 200_000,
             t0: float = 0.6, t1: float = 0.05, retries: int = 3, init: Plan | None = None,
             init_pos: Sequence[int] | None = None, bill_moves: int = 0, segment: float = 0.1,
             log=print) -> Plan:
    """Search placement, check assignment and start traps for a short round.

    One ancilla per trap (x = m/2, A = 1).  Stage 1 lowers the phase-length targets until
    the window penalty can no longer reach zero within ``moves`` annealing moves; stage 2
    anneals the exact bill inside the best windows found.
    """
    m2 = len(hx)
    if len(hz) != m2:
        raise ValueError("Cyclone pairs one X and one Z check per ancilla")
    x = m2 if x is None else x
    if x != m2:
        raise NotImplementedError("the optimizer keeps one ancilla per trap: x = m/2")
    cap = capacity - 1
    if cap * x < n:
        raise ValueError(f"{x} traps of {cap} data cannot hold {n} data")
    rng = random.Random(seed)
    s = _Search(n, hx, hz, x, cap, rng, capacity)
    if init is not None:
        pos = list(init.data_trap)
        sx = [0] * m2
        sz = [0] * m2
        # ancilla k does x_rows[k] from anc_start[k]; its Z start is after the X phase
        kx = plan_cost(init)["steps_X"]
        for k in range(m2):
            sx[init.x_rows[k]] = init.anc_start[k]
            sz[init.z_rows[k]] = (init.anc_start[k] + kx) % x
        s.set_layout(pos, sx, sz)
    else:
        if init_pos is not None:
            pos = list(init_pos)
        else:
            order = _spectral_order(n, list(hx) + list(hz), rng)
            pos = [0] * n
            for i, d in enumerate(order):
                pos[d] = min(x - 1, (i * x) // n)
        s.set_layout(pos, exact_starts(pos, hx, x)[1], exact_starts(pos, hz, x)[1])
    s.k = list(s.lengths())
    log(f"[{name}] start: windows kX={s.k[0]} kZ={s.k[1]}")
    best = (tuple(s.k), s.snapshot())
    t_end = time.time() + budget_s
    stuck = [False, False]
    while moves and time.time() < t_end and not all(stuck):
        ph = 0 if (s.k[0] >= s.k[1] and not stuck[0]) or stuck[1] else 1
        s.k[ph] -= 1
        left = -1
        for attempt in range(retries):          # each retry continues from where the last stopped
            left = s.anneal_feasible(moves * (attempt + 1), t0, t1, segment=segment)
            if left == 0 or time.time() > t_end:
                break
        if left == 0:
            s.k = list(s.lengths())
            best = (tuple(s.k), s.snapshot())
            stuck = [False, False]
            log(f"[{name}] feasible kX={s.k[0]} kZ={s.k[1]}  ({budget_s - (t_end - time.time()):.0f} s)")
        else:
            s.k[ph] += 1
            s.set_layout(*best[1])
            stuck[ph] = True
    s.k = list(best[0])
    s.set_layout(*best[1])
    plan = _to_plan(name, n, hx, hz, x, capacity, s)
    log(f"[{name}] stage 1 done: {plan_cost(plan)}")
    if bill_moves:
        s.k = list(s.lengths())
        before = plan_cost(plan)["time_us"]
        got = s.anneal_bill(bill_moves)
        cand = _to_plan(name, n, hx, hz, x, capacity, s)
        cost = plan_cost(cand)
        if abs(cost["time_us"] - got) > 1e-6:
            raise AssertionError(f"stage 2 priced {got}, the writer's loop prices {cost['time_us']}")
        if cost["time_us"] <= before:
            plan = cand
        log(f"[{name}] stage 2 done: {plan_cost(plan)}")
    return plan


def sat_layout(n: int, hx: list[list[int]], hz: list[list[int]], capacity: int, kx: int,
               kz: int, *, hint: tuple | None = None, time_limit: float | None = None,
               solver: str = "cd19", one_layer: bool = False) -> tuple[bool | None, tuple | None]:
    """Decide exactly whether phase lengths (kX, kZ) are reachable (python-sat; m/2 == x).

    Order-encoded data traps, Q[d][t] = (trap(d) >= t), with the one-hot view P[d][t] for
    the capacity counters (at most C - 1 data per trap); for each basis a permutation
    S[r][s] of checks onto start traps; and for every check r, start s and data d of r the
    window clause S[r][s] -> s <= trap(d) < s + k (wrapping round the ring).  X check 0
    starts in trap 0 (rotation symmetry).  ``hint`` = (data traps, X starts, Z starts) sets
    the solver's phases.  ``one_layer`` also keeps any two data of one check out of the same
    trap, so every step does exactly one gate layer.  Returns (True, layout), (False, None)
    when no layout exists, or
    (None, None) when ``time_limit`` seconds ran out first -- the query then runs in a child
    process that is terminated at the deadline (CaDiCaL cannot be interrupted in-process).
    """
    if time_limit is None:
        return _sat_solve(n, hx, hz, capacity, kx, kz, hint, solver, one_layer)
    import subprocess
    import sys

    root = str(Path(__file__).resolve().parents[2])
    payload = json.dumps({"n": n, "hx": hx, "hz": hz, "capacity": capacity, "kx": kx, "kz": kz,
                          "hint": hint, "solver": solver, "one_layer": one_layer})
    code = ("import sys; sys.path.insert(0, sys.argv[1]); "
            "from qccd.repro.opt_cyclone import _sat_main; _sat_main()")
    try:
        out = subprocess.run([sys.executable, "-c", code, root], input=payload,
                             capture_output=True, text=True, timeout=time_limit,
                             check=False)
    except subprocess.TimeoutExpired:                 # the child is killed
        return None, None
    if out.returncode != 0:
        raise RuntimeError(f"the SAT child failed: {out.stderr[-2000:]}")
    got = json.loads(out.stdout)
    return got["ok"], (tuple(got["layout"]) if got["layout"] else None)


def _sat_main() -> None:
    """Child-process entry for `sat_layout(..., time_limit=...)`: JSON in, JSON out."""
    import sys

    a = json.loads(sys.stdin.read())
    hint = tuple(a["hint"]) if a["hint"] else None
    ok, lay = _sat_solve(a["n"], a["hx"], a["hz"], a["capacity"], a["kx"], a["kz"], hint,
                         a["solver"], a.get("one_layer", False))
    sys.stdout.write(json.dumps({"ok": ok, "layout": list(lay) if lay else None}))


def _sat_solve(n: int, hx: list[list[int]], hz: list[list[int]], capacity: int, kx: int,
               kz: int, hint: tuple | None, solver: str,
               one_layer: bool = False) -> tuple[bool, tuple | None]:
    from pysat.card import CardEnc, EncType
    from pysat.formula import CNF, IDPool
    from pysat.solvers import Solver

    x = len(hx)
    pool = IDPool()

    def q(d: int, t: int):
        return True if t <= 0 else (False if t >= x else pool.id(("q", d, t)))

    cnf = CNF()
    for d in range(n):
        for t in range(2, x):
            cnf.append([-q(d, t), q(d, t - 1)])
    P = [[pool.id(("p", d, t)) for t in range(x)] for d in range(n)]
    for d in range(n):
        for t in range(x):
            p, a, b = P[d][t], q(d, t), q(d, t + 1)
            if a is not True:
                cnf.append([-p, a])
            if b is not False:
                cnf.append([-p, -b])
            back = [p] + ([] if a is True else [-a]) + ([] if b is False else [b])
            cnf.append(back)
    for t in range(x):
        cnf.extend(CardEnc.atmost(lits=[P[d][t] for d in range(n)], bound=capacity - 1,
                                  vpool=pool, encoding=EncType.seqcounter).clauses)
    S = [[[pool.id(("s", ph, r, s)) for s in range(x)] for r in range(x)] for ph in (0, 1)]
    for ph in (0, 1):
        for r in range(x):
            cnf.extend(CardEnc.equals(lits=S[ph][r], bound=1, vpool=pool,
                                      encoding=EncType.seqcounter).clauses)
        for s in range(x):
            cnf.extend(CardEnc.atmost(lits=[S[ph][r][s] for r in range(x)], bound=1,
                                      vpool=pool, encoding=EncType.seqcounter).clauses)
    for ph, rows, k in ((0, hx, kx), (1, hz, kz)):
        for r, row in enumerate(rows):
            for s in range(x):
                sv = S[ph][r][s]
                for d in row:
                    a = q(d, s)
                    if s + k <= x:
                        b = q(d, s + k)
                        if a is not True:
                            cnf.append([-sv, a])
                        if b is not False:
                            cnf.append([-sv, -b])
                    else:
                        b = q(d, s + k - x)
                        if a is True or b is False:
                            continue
                        cnf.append([-sv] + ([] if a is False else [a]) + ([] if b is True else [-b]))
    if one_layer:
        for row in list(hx) + list(hz):
            for t in range(x):
                for i, a in enumerate(row):
                    for b in row[i + 1:]:
                        cnf.append([-P[a][t], -P[b][t]])
    cnf.append([S[0][0][0]])
    with Solver(name=solver, bootstrap_with=cnf.clauses) as sv:
        if hint is not None:
            pos, sx, sz = hint
            sh = sx[0]                                   # rotate so X check 0 starts in trap 0
            pos = [(p - sh) % x for p in pos]
            st = ([(s - sh) % x for s in sx], [(s - sh) % x for s in sz])
            phases = []
            for d in range(n):
                phases += [pool.id(("q", d, t)) * (1 if pos[d] >= t else -1) for t in range(1, x)]
                phases += [P[d][t] * (1 if pos[d] == t else -1) for t in range(x)]
            for ph in (0, 1):
                for r in range(x):
                    phases += [S[ph][r][s] * (1 if st[ph][r] == s else -1) for s in range(x)]
            sv.set_phases(phases)
        if not sv.solve():
            return False, None
        model = {v for v in sv.get_model() if v > 0}
    pos = [next(t for t in range(x) if P[d][t] in model) for d in range(n)]
    st = [[next(s for s in range(x) if S[ph][r][s] in model) for r in range(x)] for ph in (0, 1)]
    return True, (pos, st[0], st[1])


def layout_of(plan: Plan) -> tuple[list[int], list[int], list[int]]:
    """A plan's layout: data traps and the start trap of every X and every Z check."""
    kx = plan_cost(plan)["steps_X"]
    sx = [0] * len(plan.hx)
    sz = [0] * len(plan.hz)
    for k in range(plan.m2):
        sx[plan.x_rows[k]] = plan.anc_start[k]
        sz[plan.z_rows[k]] = (plan.anc_start[k] + kx) % plan.x
    return list(plan.data_trap), sx, sz


def plan_from_layout(name: str, n: int, hx: list[list[int]], hz: list[list[int]],
                     capacity: int, pos: Sequence[int], x_start: Sequence[int],
                     z_start: Sequence[int], note: str = "") -> Plan:
    """A layout -- data traps, and for every X and every Z check the trap its window starts
    in (each a permutation of the traps) -- as a plan: ancilla k starts in trap k, does the X
    check that starts there, and then the Z check that starts where it stands when the X
    phase ends."""
    x = len(hx)
    s = _Search(n, hx, hz, x, capacity - 1, random.Random(0), capacity)
    s.set_layout(list(pos), list(x_start), list(z_start))
    s.k = list(s.lengths())
    p = _to_plan(name, n, hx, hz, x, capacity, s)
    if note:
        p.note = note
    return p


def _to_plan(name: str, n: int, hx, hz, x: int, capacity: int, s: _Search) -> Plan:
    """Ancilla k starts in trap k; it does the X check that starts there and the Z check
    that starts where it stands when the X phase ends."""
    m2 = len(hx)
    kx = s.lengths()[0]
    x_rows = [s.at[0][k] for k in range(m2)]
    z_rows = [s.at[1][(k + kx) % x] for k in range(m2)]
    order: dict[int, list[int]] = {t: sorted(s.members[t]) for t in range(x)}
    return Plan(name=name, n=n, x=x, capacity=capacity, hx=[list(r) for r in hx],
                hz=[list(r) for r in hz], data_trap=list(s.pos), anc_start=list(range(m2)),
                x_rows=x_rows, z_rows=z_rows, data_order=order,
                note=f"opt_cyclone.optimize (windows {s.k[0]}/{s.k[1]})")
