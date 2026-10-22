"""Cyclone's round under Cyclone's own cost model, run in the circuit's own order.

Cyclone (Khan et al., HPCA 2026, arXiv 2511.15910) runs a syndrome round on a ring of x
traps: one ancilla per trap, every ancilla does one X check and then one Z check, and all
ancillas move one trap clockwise per lockstep step.  A step costs

    g * max(1, P)  +  3 * g * A  +  s

where P is the largest number of CX any trap does in the step (the gates of one trap are
serial, g = max(100, 13.33 C - 54) us), 3gA is a gate swap charged for every ancilla every
step, and s = 165 us is split 80 + move 5 + merge 80.  One 100 us H layer opens the round and
one closes it.  A phase runs while any of its checks has a gate left (the artifact's
``while (checkEmpty(...) == False)``).

The order.  The round is a circuit: X check i is a list of CX(ancilla, data) and Z check i
a list of CX(data, ancilla), both on ancilla i, in the order the input lists them
(`importers.cyclone_circuit`, the ``*_true_round.json`` files).  Our schedules keep that
order exactly: every ancilla runs its check's CX gates in the listed order, and every data
qubit meets its checks in the order the circuit lists them (X check 0, 1, 2, ... then Z
check 0, 1, 2, ...).  The gates of a check commute, and so do the checks, so the artifact's
loop -- each ancilla does whichever gates of its check it finds in its trap -- computes the
same syndrome; but the order of the input circuit is its author's, and a compiler may not
change it.  So the X check and the Z check of ancilla i stay on one ion, a{n+i}, as in the
input, and the ring decides only WHEN each gate can run: ancilla k, starting in trap s_k,
is in trap (s_k + t) mod x at step t, so its gate with data d (in trap p_d) runs at the
first step t >= (its previous gate, the data's previous gate + 1) with t = p_d - s_k mod x.
Where an ancilla passes a data qubit it is not yet allowed to touch, it comes round again a
full rotation later.  `strict_times` computes that, gate by gate.

The artifact's own loop (``order="artifact"``: per step, each ancilla does the gates of its
check it has not done that are in its trap) is kept only to replay the artifact's own plan
(`naive_plan`, `artifact_plan`) and its printed numbers.

A `Plan` says what the model leaves free:

    which trap each data qubit sits in        (data per trap <= C - A)
    where each ancilla starts                 (one per trap, A = 1)

(``x_rows``/``z_rows``, which check each ancilla measures, are the identity for our plans;
a replayed artifact plan carries its own.)  `write` turns a plan into a `TimedSchedule` with
the event rules `importers.import_cyclone` uses for the artifact's compiles: gates of a step
at t0 + j*g in their trap, then per ancilla a split that carries the folded gate swap, a move
across the degree-2 junction and a merge into the next trap.  `verify` replays the saved
schedule with `timed.check` under `models.CYCLONE` (circuit order ``strict`` for our plans)
and `models.cyclone_duration` against the circuit built from the TRUE check matrices, and
runs a noiseless stim memory experiment over the executed CX order.

``sat_layout`` decides exactly whether a round with L_X = kx steps (exactly) and L_Z <= kz
exists: data traps and (optionally) start traps one-hot, every gate's step order-encoded
(windows may exceed a rotation), the row order (<=, equal only in one trap) and the data
order (<) as implications between the order literals.  ``optimize`` finds the least X
phase for each start-trap seed, then scans L_X for the least Z phase that still beats the
best round so far, and keeps the cheapest round the exact propagation confirms.
``reproduce_ours`` writes the published rounds (``Reproduce/khan2026cyclone/ours``) and
the plan each was written from.
"""

from __future__ import annotations

import dataclasses
import json
import math
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .importers import cyclone_circuit, cyclone_device
from .models import CYCLONE, cyclone_duration
from .timed import Event, Report, TimedSchedule, check

__all__ = ["Plan", "gate_us", "ring_device", "naive_plan", "write", "circuit_for",
           "verify", "stim_check", "step_cost", "plan_cost", "strict_times", "optimize",
           "sat_layout", "hgp", "load_hgp_checks", "load_bb_checks", "artifact_plan",
           "true_rows", "reproduce_ours"]

SPLIT_US = 80.0
MERGE_US = 80.0
MOVE_US = 5.0
H_US = 100.0

#: Our plans' order (each qubit in its input order); the artifact's loop is "artifact".
STRICT = "strict"
ARTIFACT = "artifact"


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

    ``hx``/``hz``   the check supports the compiler EXECUTES (rows of data indices, in the
                    circuit's order); for a correct compile these are the code's true checks
    ``data_trap``   data qubit q -> trap
    ``anc_start``   ancilla k -> the trap it starts in (ancilla k is ion ``a{n+k}``)
    ``x_rows``      ancilla k -> the row of ``hx`` it measures; ``z_rows`` likewise
    ``data_order``  optional: trap -> data in the order the chain lists them
    ``order``       "strict" (ours: every qubit in its input order, `strict_times`) or
                    "artifact" (the artifact's loop, to replay the artifact's own plan)
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
    order: str = STRICT

    @property
    def m2(self) -> int:
        return len(self.anc_start)

    def to_json(self) -> dict:
        return {"name": self.name, "n": self.n, "x": self.x, "capacity": self.capacity,
                "hx": self.hx, "hz": self.hz, "data_trap": self.data_trap,
                "anc_start": self.anc_start, "x_rows": self.x_rows, "z_rows": self.z_rows,
                "data_order": ({str(k): v for k, v in self.data_order.items()}
                               if self.data_order else None),
                "note": self.note, "order": self.order}

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> "Plan":
        order = d.get("data_order")
        return cls(name=d["name"], n=int(d["n"]), x=int(d["x"]), capacity=int(d["capacity"]),
                   hx=[list(r) for r in d["hx"]], hz=[list(r) for r in d["hz"]],
                   data_trap=list(d["data_trap"]), anc_start=list(d["anc_start"]),
                   x_rows=list(d["x_rows"]), z_rows=list(d["z_rows"]),
                   data_order=({int(k): list(v) for k, v in order.items()} if order else None),
                   note=d.get("note", ""), order=d.get("order", STRICT))

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.write_text(json.dumps(self.to_json(), separators=(",", ":")), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path) -> "Plan":
        return cls.from_json(json.loads(Path(path).read_text(encoding="utf-8")))

    def validate(self) -> None:
        """The model's static rules: one ancilla per trap (A = 1), data per trap <= C - 1,
        every data qubit placed, every check row used once per basis.  A strict plan also
        keeps every check on the ancilla the input circuit gives it (X and Z check k on
        ancilla k): moving checks between ancillas would change the order in which a data
        qubit meets them."""
        if self.order not in (STRICT, ARTIFACT):
            raise ValueError(f"unknown order {self.order!r}")
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
        if self.order == STRICT and (self.x_rows != list(range(self.m2))
                                     or self.z_rows != list(range(self.m2))):
            raise ValueError("a strict plan keeps X and Z check k on ancilla k, as the circuit does")


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
    rows the compiler built), ancilla i in trap i mod x, check i -> ancilla i mod (m/2), run
    by the artifact's loop.

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
                note="the artifact's naive plan (round-robin data, check i -> ancilla i)",
                order=ARTIFACT)


# ---------------------------------------------------------------------------- the writer


def _trap_steps(plan: Plan, rows: list[list[int]], start: list[int]) -> list[list[list[int]]]:
    """The ARTIFACT's loop for one phase: per step, for each ancilla, the data of its check
    in its current trap that it has not met yet, whatever their place in the check.
    Returns steps[step][k] = data."""
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


def _strict_steps(x: int, data_trap: Sequence[int], rows: Sequence[Sequence[int]],
                  start: Sequence[int]) -> list[list[list[int]]]:
    """One phase in the circuit's order: ancilla k runs rows[k] in its listed order, and
    each data qubit meets its rows in row order.  A gate runs at the first step t no earlier
    than the ancilla's previous gate (the same step only in the same trap) and later than
    the data's previous gate, with the ancilla in the data's trap: t = p_d - s_k (mod x).
    Rows are processed in index order, which is a topological order of both constraints.
    Returns steps[step][k] = the data ancilla k meets at that step, in row order."""
    n = len(data_trap)
    last = [-1] * n
    times: list[list[int]] = []
    for k, row in enumerate(rows):
        s = start[k]
        t = 0
        ts = []
        for d in row:
            lb = max(t, last[d] + 1)
            t = lb + ((data_trap[d] - s - lb) % x)
            last[d] = t
            ts.append(t)
        times.append(ts)
    L = 1 + max((t for ts in times for t in ts), default=-1)
    steps: list[list[list[int]]] = [[[] for _ in rows] for _ in range(max(L, 0))]
    for k, (row, ts) in enumerate(zip(rows, times)):
        for d, t in zip(row, ts):
            steps[t][k].append(d)
    return steps


def _phase_steps(plan: Plan, rows: list[list[int]], start: list[int]) -> list[list[list[int]]]:
    if plan.order == ARTIFACT:
        return _trap_steps(plan, rows, start)
    return _strict_steps(plan.x, plan.data_trap, rows, start)


def strict_times(plan: Plan) -> dict:
    """Per phase, the step of every gate of a strict plan: {"X": [[t per gate of row k]],
    "Z": ..., "L_X", "L_Z"}."""
    out: dict[str, Any] = {}
    where = list(plan.anc_start)
    for phase, rows_all, idx in (("X", plan.hx, plan.x_rows), ("Z", plan.hz, plan.z_rows)):
        rows = [rows_all[i] for i in idx]
        steps = _strict_steps(plan.x, plan.data_trap, rows, where)
        at = [[0] * len(r) for r in rows]
        for t, now in enumerate(steps):
            for k, ds in enumerate(now):
                for d in ds:
                    at[k][rows[k].index(d)] = t
        out[phase] = at
        out[f"L_{phase}"] = len(steps)
        where = [(q + len(steps)) % plan.x for q in where]
    return out


def write(plan: Plan, *, name: str | None = None) -> TimedSchedule:
    """A plan -> timed primitives, with `import_cyclone`'s event rules.

    Phase X runs until every ancilla has met all of its X check, then phase Z starts from
    wherever the ancillas are and runs until every Z check is done.  Each step: gate layer j
    at t0 + j*g in each trap (CX(ancilla, data) in X, CX(data, ancilla) in Z), an ancilla's
    gates of the step in its check's order; the step's gate time is g * max(1, P); then every
    ancilla splits (the 3gA gate swap folded into the split, `meta.swap_us`), moves across
    its degree-2 junction and merges into the next trap.  One 100 us H layer (an ion-less
    `gate1`) at either end.  The plan's ``order`` says which gates a step runs.
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
              "P_Z": summary["P"]["Z"], "g_us": g, "swap_us": swap, "A": A,
              "order": plan.order}
    return TimedSchedule(name=name or plan.name, device=dev, chains=chains, events=events,
                         source={"tool": "qccd.repro.opt_cyclone.write", "plan": plan.name,
                                 "note": plan.note, "x": x, "capacity": plan.capacity,
                                 "order": plan.order},
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
    the plan gives it (the rows are only relabelled, never changed; a strict plan keeps
    every row on its own ancilla)."""
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
    """`timed.check` under Cyclone's profile and duration law against the true circuit --
    in strict circuit order for a strict plan (each qubit exactly in its input order), in
    the profile's own for a replayed artifact plan -- plus the stim memory check.  ``ok``
    only when both are clean."""
    prof = CYCLONE if plan.order == ARTIFACT else dataclasses.replace(CYCLONE, circuit_order="strict")
    rep: Report = check(sched, prof, duration=cyclone_duration,
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
    start traps and its check assignment (matched to rows of the executed matrices), run by
    the artifact's loop."""
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
                note="read from the artifact's schedule.json", order=ARTIFACT)


# ------------------------------------------------------------------------ the optimizer


def sat_layout(n: int, hx: list[list[int]], hz: list[list[int]], capacity: int, kx: int,
               kz: int, *, starts: Sequence[int] | None = None, x_only: bool = False,
               time_limit: float | None = None, solver: str = "cd19"
               ) -> tuple[bool | None, tuple | None]:
    """Decide exactly whether a strict round fits L_X = kx steps (exactly; with ``x_only``,
    at most kx and no Z phase) and L_Z <= kz, one ancilla per trap (x = len(hx)).

    Data traps P[d][t] are one-hot (at most C - 1 per trap); start traps S[k][t] are a
    permutation with ancilla 0 in trap 0 (rotation symmetry), or fixed to ``starts``.  Each
    gate's step is order-encoded, G[g][u] <-> T(g) >= u, T < kx (X) or kz (Z), so a window
    may span several rotations; T(g) = tau puts the gate's data in trap
    (s_k + off + tau) mod x, off = kx in the Z phase.  The row order is G[g][u] -> G[g'][u]
    (a later gate of the check, the same step only in the same trap), the data order
    G[g][u] -> G[g'][u+1]; and some X gate takes step kx - 1.  Returns (True, (data traps,
    start traps)), (False, None) when none exists, or (None, None) when ``time_limit``
    seconds ran out first (the query then runs in a child process that is terminated at
    the deadline: CaDiCaL cannot be interrupted in-process).
    """
    if time_limit is None:
        return _sat_solve(n, hx, hz, capacity, kx, kz, starts, x_only, solver)
    import subprocess
    import sys

    root = str(Path(__file__).resolve().parents[2])
    payload = json.dumps({"n": n, "hx": hx, "hz": hz, "capacity": capacity, "kx": kx, "kz": kz,
                          "starts": list(starts) if starts is not None else None,
                          "x_only": x_only, "solver": solver})
    code = ("import sys; sys.path.insert(0, sys.argv[1]); "
            "from qccd.repro.opt_cyclone import _sat_main; _sat_main()")
    try:
        out = subprocess.run([sys.executable, "-c", code, root], input=payload,
                             capture_output=True, text=True, timeout=time_limit, check=False)
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
    ok, lay = _sat_solve(a["n"], a["hx"], a["hz"], a["capacity"], a["kx"], a["kz"], a["starts"],
                         a["x_only"], a["solver"])
    sys.stdout.write(json.dumps({"ok": ok, "layout": list(lay) if lay else None}))


def _sat_solve(n: int, hx: list[list[int]], hz: list[list[int]], capacity: int, kx: int,
               kz: int, starts: Sequence[int] | None, x_only: bool,
               solver: str) -> tuple[bool, tuple | None]:
    from pysat.card import CardEnc, EncType
    from pysat.formula import IDPool
    from pysat.solvers import Solver

    x = len(hx)
    pool = IDPool()
    cl: list[list[int]] = []
    false = pool.id("false")
    cl.append([-false])
    P = [[pool.id(("p", d, t)) for t in range(x)] for d in range(n)]
    for d in range(n):
        cl += CardEnc.equals(P[d], 1, vpool=pool, encoding=EncType.seqcounter).clauses
    for t in range(x):
        cl += CardEnc.atmost([P[d][t] for d in range(n)], capacity - 1, vpool=pool,
                             encoding=EncType.seqcounter).clauses
    S = None
    if starts is None:
        S = [[pool.id(("s", k, t)) for t in range(x)] for k in range(x)]
        for k in range(x):
            cl += CardEnc.equals(S[k], 1, vpool=pool, encoding=EncType.seqcounter).clauses
        for t in range(x):
            cl += CardEnc.equals([S[k][t] for k in range(x)], 1, vpool=pool,
                                 encoding=EncType.seqcounter).clauses
        cl.append([S[0][0]])
    phases = [(0, hx, kx, 0)] + ([] if x_only else [(1, hz, kz, kx)])
    G: dict[tuple, tuple[list, int]] = {}
    for ph, rows, k, off in phases:
        for r, row in enumerate(rows):
            for i, d in enumerate(row):
                key = (ph, r, i)
                g = [None] + [pool.id(("G",) + key + (u,)) for u in range(1, k)]
                G[key] = (g, k)
                for u in range(1, k - 1):
                    cl.append([-g[u + 1], g[u]])
                if starts is None and k > x:
                    # free starts over a long window: through the step's residue mod x,
                    # Rg[rho] <- T = rho (mod x), then S[r][sig] & Rg[rho] -> P[d][sig+off+rho]
                    # (x*x clauses a gate instead of x*k)
                    Rg = [pool.id(("R",) + key + (rho,)) for rho in range(x)]
                    for tau in range(k):
                        lits = ([-g[tau]] if tau >= 1 else []) + ([g[tau + 1]] if tau + 1 < k else [])
                        cl.append(lits + [Rg[tau % x]])
                    for sig in range(x):
                        for rho in range(x):
                            cl.append([-S[r][sig], -Rg[rho], P[d][(sig + off + rho) % x]])
                    continue
                for tau in range(k):
                    lits = ([-g[tau]] if tau >= 1 else []) + ([g[tau + 1]] if tau + 1 < k else [])
                    if starts is not None:
                        cl.append(lits + [P[d][(starts[r] + off + tau) % x]])
                    else:
                        for sig in range(x):
                            cl.append(lits + [-S[r][sig], P[d][(sig + off + tau) % x]])

    def ge(key: tuple, u: int):
        g, k = G[key]
        return True if u <= 0 else (false if u >= k else g[u])

    def implies(a, b) -> None:
        if a == false or b is True:
            return
        cl.append([b] if a is True else [-a, b])

    for ph, rows, k, off in phases:
        cols: dict[int, list[tuple[int, int]]] = {}
        for r, row in enumerate(rows):
            for i, d in enumerate(row):
                cols.setdefault(d, []).append((r, i))
                if i:                                   # the check's order (<=)
                    for u in range(1, k):
                        implies(ge((ph, r, i - 1), u), ge((ph, r, i), u))
        for d, gs in cols.items():                      # the data's order (<)
            for (r1, i1), (r2, i2) in zip(gs, gs[1:]):
                for u in range(0, k):
                    implies(ge((ph, r1, i1), u), ge((ph, r2, i2), u + 1))
    if not x_only:
        cl.append([G[(0, r, i)][0][kx - 1] for r, row in enumerate(hx) for i in range(len(row))])
    with Solver(name=solver, bootstrap_with=cl) as sv:
        if not sv.solve():
            return False, None
        model = {v for v in sv.get_model() if v > 0}
    pos = [next(t for t in range(x) if P[d][t] in model) for d in range(n)]
    st = list(starts) if starts is not None else \
        [next(t for t in range(x) if S[k][t] in model) for k in range(x)]
    return True, (pos, st)


def _bill(x: int, g: float, pos: Sequence[int], starts: Sequence[int], hx, hz) -> float:
    """`plan_cost`'s time for a strict plan, without building the plan (the polish's inner
    loop): the same propagation, phase by phase."""
    total = 2 * H_US
    fixed = 3 * g + SPLIT_US + MOVE_US + MERGE_US
    where = list(starts)
    n = len(pos)
    for rows in (hx, hz):
        last = [-1] * n
        width: dict[int, int] = {}
        L = 0
        for k, row in enumerate(rows):
            s, t, prev, run = where[k], 0, -1, 0
            for d in row:
                lb = max(t, last[d] + 1)
                t = lb + ((pos[d] - s - lb) % x)
                run = run + 1 if t == prev else 1
                if width.get(t, 0) < run:
                    width[t] = run
                prev = last[d] = t
            L = max(L, t + 1)
        total += L * fixed + g * sum(max(1, width.get(j, 0)) for j in range(L))
        where = [(q + L) % x for q in where]
    return total


def polish(plan: Plan, *, moves: int = 50_000, seed: int = 1, temp: float = 60.0,
           budget_s: float | None = None, log=print) -> Plan:
    """Local search on the exact bill from a strict plan: move or swap data between traps
    (capacity permitting) and swap two ancillas' start traps, accepting a worse bill with
    probability exp(-delta / T), T falling from ``temp`` to 1 us.  The solver counts steps
    only; this also trims the extra gate layers (two gates of one check in one trap at one
    step) and whatever the greedy propagation leaves.  ``budget_s`` stops it early (the
    temperature schedule still runs over ``moves``)."""
    import random

    rng = random.Random(seed)
    x, cap, n = plan.x, plan.capacity - 1, plan.n
    g = gate_us(plan.capacity)
    hx = [plan.hx[i] for i in plan.x_rows]
    hz = [plan.hz[i] for i in plan.z_rows]
    pos, st = list(plan.data_trap), list(plan.anc_start)
    members: list[list[int]] = [[] for _ in range(x)]
    for d, t in enumerate(pos):
        members[t].append(d)
    at = {s: k for k, s in enumerate(st)}
    cur = _bill(x, g, pos, st, hx, hz)
    best = (cur, list(pos), list(st))
    start = cur
    t_end = time.time() + budget_s if budget_s else None
    for it in range(moves):
        if t_end is not None and (it & 255) == 0 and time.time() > t_end:
            break
        T = temp * (1.0 / temp) ** (it / moves)
        if rng.random() < 0.7:
            d = rng.randrange(n)
            a = pos[d]
            b = (a + rng.randint(-3, 3)) % x if rng.random() < 0.8 else rng.randrange(x)
            if a == b:
                continue
            e = -1
            if len(members[b]) >= cap or rng.random() < 0.5:
                if not members[b]:
                    continue
                e = members[b][rng.randrange(len(members[b]))]
            pos[d] = b
            if e >= 0:
                pos[e] = a
            new = _bill(x, g, pos, st, hx, hz)
            if new <= cur or rng.random() < math.exp(-(new - cur) / T):
                cur = new
                members[a].remove(d)
                members[b].append(d)
                if e >= 0:
                    members[b].remove(e)
                    members[a].append(e)
            else:
                pos[d] = a
                if e >= 0:
                    pos[e] = b
        else:
            s1 = rng.randrange(x)
            s2 = (s1 + rng.randint(-4, 4)) % x if rng.random() < 0.8 else rng.randrange(x)
            if s1 == s2:
                continue
            k1, k2 = at[s1], at[s2]
            st[k1], st[k2] = s2, s1
            new = _bill(x, g, pos, st, hx, hz)
            if new <= cur or rng.random() < math.exp(-(new - cur) / T):
                cur = new
                at[s1], at[s2] = k2, k1
            else:
                st[k1], st[k2] = s1, s2
        if cur < best[0]:
            best = (cur, list(pos), list(st))
    log(f"[{plan.name}] polish: {start:.0f} -> {best[0]:.0f} us")
    out = _plan(plan.name, n, plan.hx, plan.hz, plan.capacity, best[1], best[2],
                plan.note + (f"; polished {start:.0f} -> {best[0]:.0f} us" if best[0] < start else ""))
    if abs(plan_cost(out)["time_us"] - best[0]) > 1e-6:
        raise AssertionError("the polish's bill and plan_cost disagree")
    return out


def _plan(name: str, n: int, hx, hz, capacity: int, pos: Sequence[int], starts: Sequence[int],
          note: str) -> Plan:
    x = len(hx)
    order = {t: [d for d in range(n) if pos[d] == t] for t in range(x)}
    return Plan(name=name, n=n, x=x, capacity=capacity, hx=[list(r) for r in hx],
                hz=[list(r) for r in hz], data_trap=list(pos), anc_start=list(starts),
                x_rows=list(range(x)), z_rows=list(range(x)), data_order=order, note=note,
                order=STRICT)


def order_bound(rows: Sequence[Sequence[int]], n: int) -> int:
    """A layout-free lower bound on a strict phase: the longest chain of the circuit's order
    in which every step to a data qubit's next check costs a step (an ancilla's next gate
    may share its step)."""
    last = [-1] * n
    longest = 0
    for row in rows:
        t = 0
        for d in row:
            t = max(t, last[d] + 1)
            last[d] = t
        longest = max(longest, t + 1)
    return longest


def _least(feasible, lo: int, hi: int | None, out_of_time) -> tuple[int | None, Any]:
    """The least k >= lo with feasible(k) True: bisection on [lo, hi] when an upper bound is
    given (and holds), else doubling from lo first.  A query that times out counts as infeasible, so
    the answer is the least k the solver could CONFIRM.  Returns (k, its witness)."""
    witness = None
    if hi is None:
        k, bad = lo, lo - 1
        while not out_of_time():
            ok, w = feasible(k)
            if ok:
                witness = (k, w)
                break
            bad = k
            k = max(k + 1, int(k * 1.5))
        if witness is None:
            return None, None
        lo, (hi, w_hi) = bad + 1, witness
    else:                                # the bound first: if it fails, nothing below can win
        ok, w_hi = feasible(hi)
        if not ok:
            return None, None
    while lo < hi and not out_of_time():
        mid = (lo + hi) // 2
        ok, w = feasible(mid)
        if ok:
            hi, w_hi = mid, w
        else:
            lo = mid + 1
    return hi, w_hi


def optimize(name: str, n: int, hx: list[list[int]], hz: list[list[int]], *, capacity: int = 5,
             starts: Sequence[str] | None = None, time_limit: float = 120.0,
             budget_s: float = 3600.0,
             kx_grid: Sequence[float] = (1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5),
             polish_moves: int = 20_000, log=print) -> Plan:
    """The shortest strict round `sat_layout` can find within ``budget_s``, confirmed by the
    writer's own propagation.

    For each start seed -- "free" (the solver also places the ancillas for the X phase,
    and those starts are kept for the round; the default on rings of up to 45 traps),
    "reversed" (ancilla k in trap -k: every ancilla reaches a trap one step after the one
    numbered before it, the order in which a data qubit must meet them; the default on
    larger rings) and "forward" (ancilla k in trap k) -- the least X phase kx_min is found
    alone, by bisection between `order_bound` and the length a plain layout already
    achieves (doubling from `order_bound` for "free"); then, for L_X = kx_min times each
    factor of ``kx_grid`` and around the best of them, the least Z phase that still beats
    the best round so far.  Each SAT query gets ``time_limit``
    seconds (a timeout counts as infeasible).  Every layout found is run by `plan_cost`
    (the greedy propagation: each gate as early as the order allows; it can only be
    earlier than the solver's steps), the cheapest bill is kept, and `polish` trims it."""
    x = len(hx)
    if len(hz) != x:
        raise ValueError("Cyclone pairs one X and one Z check per ancilla, one ancilla per trap")
    if (capacity - 1) * x < n:
        raise ValueError(f"{x} traps of {capacity - 1} data cannot hold {n} data")
    seeds = list(starts) if starts is not None else (["free"] if x <= 45 else ["reversed"])
    t_start = time.time()
    out_of_time = lambda: time.time() - t_start > budget_s
    plain = [(d * x) // n for d in range(n)]                 # data in index order
    best: tuple[float, Plan] | None = None

    def best_steps() -> int:
        c = plan_cost(best[1])
        return c["steps_X"] + c["steps_Z"]

    def keep(plan: Plan) -> bool:
        nonlocal best
        cost = plan_cost(plan)
        log(f"[{name}] {plan.note}: steps {cost['steps_X']}/{cost['steps_Z']}, {cost['time_us']:.0f} us")
        if best is None or cost["time_us"] < best[0]:
            best = (cost["time_us"], plan)
            return True
        return False

    for seed in seeds:
        st = None if seed == "free" else (
            [(-k) % x for k in range(x)] if seed == "reversed" else list(range(x)))
        ref = _plan(name, n, hx, hz, capacity, plain, st or [(-k) % x for k in range(x)],
                    f"{seed} starts, data in index order")
        keep(ref)

        def fx(k: int):
            ok, lay = sat_layout(n, hx, hz, capacity, k, k, starts=st, x_only=True,
                                 time_limit=time_limit)
            log(f"[{name}/{seed}] X alone in {k}: {ok} ({time.time() - t_start:.0f} s)")
            return bool(ok), lay

        kx_min, lay_x = _least(fx, order_bound(hx, n), plan_cost(ref)["steps_X"] if st else None,
                               out_of_time)
        if kx_min is None:
            continue
        if st is None:                   # "free": keep the starts the X phase chose
            st = list(lay_x[1])
            seed = "X-phase"
        # L_X = kx fixes where the ancillas stand when the Z phase starts, so the least kx is
        # rarely the best round: scan kx upwards, asking only for a Z phase short enough to
        # beat the best round so far, then refine around the best kx
        tried: set[int] = set()

        def try_kx(kx: int) -> None:
            if kx in tried or out_of_time():
                return
            tried.add(kx)
            hi = best_steps() - kx - 1
            if hi < order_bound(hz, n):
                return

            def fz(k: int, kx=kx):
                ok, lay = sat_layout(n, hx, hz, capacity, kx, k, starts=st, time_limit=time_limit)
                log(f"[{name}/{seed}] L_X = {kx}, Z in {k}: {ok} ({time.time() - t_start:.0f} s)")
                return bool(ok), lay

            kz, lay = _least(fz, order_bound(hz, n), hi, out_of_time)
            if kz is not None and keep(_plan(name, n, hx, hz, capacity, lay[0], lay[1],
                                             f"opt_cyclone.optimize ({seed} starts, SAT "
                                             f"L_X={kx} L_Z<={kz})")):
                best_kx[0] = kx

        best_kx = [None]
        for f in kx_grid:
            try_kx(int(round(kx_min * f)))
        step = max(1, int(kx_min * 0.08))
        while step >= 1 and best_kx[0] is not None and not out_of_time():
            centre = best_kx[0]
            for kx in (centre - step, centre + step):
                if kx >= kx_min:
                    try_kx(kx)
            if best_kx[0] == centre:
                step //= 2
    if best is None:
        raise RuntimeError(f"[{name}] no strict round found within the limits")
    return polish(best[1], moves=polish_moves, log=log) if polish_moves else best[1]


# ------------------------------------------------------------------- the published runs


def true_rows(pairs: Sequence[Sequence[str]]) -> tuple[int, list[list[int]], list[list[int]]]:
    """A round as `importers.cyclone_circuit` writes it (CX(a, d) for the X checks, then
    CX(d, a) for the Z checks) -> (n, hx, hz), row k being ancilla a{n+k}'s check with its
    data in the circuit's order."""
    xs: dict[int, list[int]] = {}
    zs: dict[int, list[int]] = {}
    for c, t in pairs:
        if c.startswith("a"):
            xs.setdefault(int(c[1:]), []).append(int(t[1:]))
        else:
            zs.setdefault(int(t[1:]), []).append(int(c[1:]))
    ancs = sorted(xs)
    n = ancs[0]
    if ancs != list(range(n, n + len(ancs))) or sorted(zs) != ancs:
        raise ValueError("every ancilla must hold one X and one Z check, numbered from n")
    return n, [xs[a] for a in ancs], [zs[a] for a in ancs]


def reproduce_ours(root: str | Path, codes: Sequence[str] | None = None, *,
                   plans: str | Path | None = None, time_limit: float = 120.0,
                   budget_s: float = 3600.0, log=print) -> list[dict]:
    """Write ``ours/<code>_ours.schedule.json.gz`` and its plan, ``ours/<code>_ours.plan.json``,
    under ``root`` (``Reproduce/khan2026cyclone``) for the catalog's Ours runs: the circuit
    of the run each is compared with, its ring and capacity, a plan, `write`, and `verify`
    in strict order before anything is written.  The plan is ``<plans>/<code>_ours.plan.json``
    when ``plans`` names a directory that has one (``root/"ours"`` re-writes the published
    schedules from their own plans, without searching), else `optimize`'s."""
    import gzip

    from . import catalog

    root = Path(root)
    paper = catalog.paper("khan2026cyclone")
    out = []
    for o in catalog.OURS["khan2026cyclone"]["runs"]:
        code = o["id"].removesuffix("_ours")
        if codes and code not in codes:
            continue
        spec = next(r for r in paper["runs"] if r["id"] == o["against"])
        pairs = json.loads((root / "circuits" / spec["circuit"]["file"]).read_text(encoding="utf-8"))
        pairs = pairs["pairs"] if isinstance(pairs, dict) else pairs
        n, hx, hz = true_rows(pairs)
        art = root / "artifact" / spec["file"]          # the same ring and capacity as theirs
        raw = json.loads(gzip.decompress(art.read_bytes()) if art.suffix == ".gz" else art.read_bytes())
        caps = {int(t["capacity"]) for t in raw["device"]["traps"]}
        if len(caps) != 1 or len(raw["device"]["traps"]) != len(hx):
            raise ValueError(f"{code}: expected one ancilla per trap on a uniform ring")
        capacity = caps.pop()
        t0 = time.time()
        given = Path(plans) / f"{code}_ours.plan.json" if plans else None
        if given is not None and given.exists():
            plan = Plan.load(given)
            if (plan.n, plan.hx, plan.hz, plan.capacity, plan.order) != (n, hx, hz, capacity, STRICT):
                raise ValueError(f"{given} is not a strict plan of this circuit and ring")
        else:
            plan = optimize(code, n, hx, hz, capacity=capacity, time_limit=time_limit,
                            budget_s=budget_s, log=log)
        sched = write(plan, name=f"{code}_ours")
        v = verify(sched, plan, hx, hz)
        if not v["ok"]:
            raise RuntimeError(f"{code}: our round fails its own checks: {v['report'].summary()} {v['stim']}")
        data = json.dumps(sched.to_json(), separators=(",", ":")).encode("utf-8")
        (root / "ours" / f"{code}_ours.schedule.json.gz").write_bytes(gzip.compress(data, mtime=0))
        plan.save(root / "ours" / f"{code}_ours.plan.json")
        row = {"code": code, "makespan_us": v["makespan_us"], "steps": (sched.claims["steps_X"],
               sched.claims["steps_Z"]), "seconds": round(time.time() - t0, 1), "note": plan.note}
        log(f"{code}: {row}")
        out.append(row)
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Our strict-order Cyclone rounds (Reproduce/khan2026cyclone/ours)")
    ap.add_argument("root", nargs="?", default=str(Path(__file__).resolve().parents[2] / "Reproduce" / "khan2026cyclone"))
    ap.add_argument("--codes", default="")
    ap.add_argument("--plans", default=None, help="a directory of <code>_ours.plan.json to write "
                    "instead of searching (the root's ours/ re-writes the published ones)")
    ap.add_argument("--time-limit", type=float, default=120.0)
    ap.add_argument("--budget", type=float, default=3600.0)
    a = ap.parse_args()
    reproduce_ours(a.root, [c for c in a.codes.split(",") if c] or None, plans=a.plans,
                   time_limit=a.time_limit, budget_s=a.budget)
