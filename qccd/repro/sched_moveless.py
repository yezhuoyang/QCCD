"""Our schedule for Moveless's benchmarks: one ancilla walks to every CNOT, in the file's order.

Moveless (Khan et al. 2025) runs one syndrome round's CNOTs, every one of them on a single
ancilla, on a line of traps filled to 5 ions with 2 slots kept for traffic, under QCCDSim's
clock.  Its compiler treats the benchmark's CNOTs as a set and runs all of a trap's CNOTs in
one visit.  Ours runs them in the order the benchmark file lists them, CNOT for CNOT, even
where two of them commute: that order is the circuit author's, and a compiler that changes
it has compiled a different circuit.  With one ancilla every CNOT is serial, so the time is
(number of CNOTs) x (gate time) plus the ancilla's travel, and the order decides the travel.

The plan:

* data qubits stay where they are placed; only the ancilla moves.  Before its first gate, the
  ancilla stands at the port end of the end trap, and for every CNOT in turn it walks, one
  neighbouring trap at a time, to the trap holding that CNOT's data qubit.  A step costs a
  split, a move and a merge; leaving a trap through the end the ancilla does not stand at,
  or passing through a trap that holds data, adds a gate swap (QCCDSim prices it at 3 gates);
* where the data stand is what the order leaves to choose, so `plan` searches it: simulated
  annealing (seeded, so the same plan every time) over which trap holds which data qubit,
  scored by the ancilla's exact walk time.  The ancilla's trap starts with at most 4 data
  (the paper's fill of 5 ions); every other trap may hold up to 6, one below the capacity of
  7 so that the ancilla still fits;
* no trap starts above the fill limit (5 ions, as in every compiler's run); a trap the plan
  gives 6 data gets its sixth by a bucket brigade at the start (each trap passes the ion at
  its near end to its neighbour toward the ancilla, which takes it at its far end -- no
  reorder anywhere), before the ancilla gets there.

A gate swap lets the ancilla leave past the data between it and the exit.  QCCDSim's chain
book, the paper's model, lifts the moving ion out in place; the physical book moves the end
ion's state to where the ancilla stood.  The two agree on who stands at each end until the
ancilla has crossed a trap of two or more data, and the walk crosses such traps more than
once, so every gate swap here names its partner by QCCDSim's book, the one the paper's
profile replays.  (Under the physical book the same operations are legal at the same times
with other partners named: the ancilla needs a swap at the same splits in both books, and a
gate swap costs 3 gates whoever it is with.)

Every event is built and timed by `sched._State` (trap, segment and junction reservations)
and the result is replayed by `timed.check` under the paper's profile, law and circuit with
strict durations, and again with the CNOTs required in exactly the file's order
(``circuit_order="strict"``), before it is returned.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import replace
from typing import Sequence

from . import sched
from .models import QCCDSIM_GATESET, QCCDSimParams, qccdsim_duration
from .timed import PortGraph, TimedSchedule, check

__all__ = ["build", "plan", "walk_us", "publish"]


def _ancilla(circ: Sequence[tuple[str, str]]) -> str:
    common = set(circ[0])
    for g in circ:
        common &= set(g)
    if len(common) != 1:
        raise sched.SchedError("this plan is for circuits whose every CNOT shares one ancilla")
    return next(iter(common))


class _QCCDSimBook(sched._State):
    """`sched._State` with QCCDSim's chain book alone naming every gate swap (who is at the
    end, how many hops): the ancilla crosses traps it has crossed before, after which the
    physical book no longer agrees on the end ion, and the paper's profile replays QCCDSim's."""

    def split_annot(self, trap: str, ion: str, side: str):
        cq = self.cq[trap]
        end = 0 if side == "L" else len(cq) - 1
        if cq[end] == ion:
            return None
        return {"kind": "gate", "with": cq[end], "hops": abs(end - cq.index(ion))}


def walk_us(seq: Sequence[int], pos: Sequence[int], cnt: Sequence[int], *, step: float,
            swap: float) -> float:
    """The ancilla's transport time for visiting the data of ``seq`` in order (``pos``: each
    data qubit's trap, by index along the line; ``cnt``: data per trap), starting in trap 0 at
    the end that faces the rest of the line: ``step`` per neighbouring trap, plus ``swap`` for
    leaving a trap that holds data through the end the ancilla does not stand at, and for each
    trap with data it passes through.  Exact for this plan: the ancilla does nothing else."""
    t = 0.0
    at, facing = 0, 1
    for q in seq:
        to = pos[q]
        if to == at:
            continue
        way = 1 if to > at else -1
        if facing != way and cnt[at]:
            t += swap
        k = at + way
        while k != to:
            t += step + (swap if cnt[k] else 0.0)
            k += way
        t += step
        at, facing = to, -way
    return t


def plan(circ: Sequence[tuple[str, str]], device: PortGraph, law: QCCDSimParams, *,
         iters: int = 150_000, seed: int = 0) -> dict:
    """Where each data qubit stands (trap sizes along the line), the initial chains at the
    fill limit, and the walk time the search found."""
    circ = [(str(a), str(b)) for a, b in circ]
    anc = _ancilla(circ)
    dev = sched._Dev(device, law)
    line = list(dev.line)
    first = line[0]
    if len(dev.ports[first]) != 1:
        raise sched.SchedError("the line must start with a trap that has one port")
    if dev.via_side[(first, line[1])] != "R":
        line.reverse()
        first = line[0]
    side0 = dev.via_side[(first, line[1])]
    fill, cap = dev.fill[first], dev.cap[first]
    if any(dev.fill[t] != fill or dev.cap[t] != cap for t in line):
        raise sched.SchedError("this plan assumes every trap has the same capacity and fill")
    names = list(dict.fromkeys(a if b == anc else b for a, b in circ))
    idx = {q: i for i, q in enumerate(names)}
    seq = [idx[a if b == anc else b] for a, b in circ]
    n, L = len(names), len(line)
    step = dev.legs[(line[0], line[1])].est
    swap = 3.0 * dev.gate_us(first)
    lim = [fill - 1] + [cap - 1] * (L - 1)         # the ancilla's trap starts at the fill limit
    occ: list[list[int]] = [[] for _ in range(n)]
    for m, q in enumerate(seq):
        occ[q].append(m)

    def chunks(per: int) -> tuple[list[int], list[int]]:
        pos, cnt, k = [0] * n, [0] * L, 0
        for q in range(n):                         # first use: the order the file meets them
            while cnt[k] >= min(per, lim[k]):
                k += 1
                if k == L:
                    raise sched.SchedError("the data do not fit the line")
            pos[q] = k
            cnt[k] += 1
        return pos, cnt

    def cost(pos, cnt) -> float:
        return walk_us(seq, pos, cnt, step=step, swap=swap)

    rng = random.Random(seed)
    best = None
    for per in (fill, cap - 1):                    # two starting points: fill-limit and full traps
        pos, cnt = chunks(per)
        cur = cost(pos, cnt)
        if best is None or cur < best[0]:
            best = (cur, list(pos), list(cnt))
        t_hi, t_lo = 400.0, 5.0
        for it in range(iters):
            temp = t_hi * (t_lo / t_hi) ** (it / iters)
            q = rng.randrange(n)
            old = pos[q]
            if rng.random() < 0.5:
                # beside a neighbour in the sequence: the trap of the data just before or after
                m = rng.choice(occ[q]) + rng.choice((-1, 1))
                if not 0 <= m < len(seq):
                    continue
                new = pos[seq[m]] + (rng.choice((-1, 1)) if rng.random() < 0.3 else 0)
            else:
                new = old + rng.choice((-2, -1, 1, 2))
            if not 0 <= new < L or new == old:
                continue
            w = None
            if cnt[new] < lim[new]:
                pos[q] = new
                cnt[old] -= 1
                cnt[new] += 1
            else:                                  # full: trade places with one ion there
                w = rng.choice([x for x in range(n) if pos[x] == new])
                pos[q], pos[w] = new, old
            v = cost(pos, cnt)
            if v <= cur or rng.random() < math.exp((cur - v) / temp):
                cur = v
                if cur < best[0]:
                    best = (cur, list(pos), list(cnt))
            elif w is None:
                pos[q] = old
                cnt[old] += 1
                cnt[new] -= 1
            else:
                pos[q], pos[w] = old, new
    walk, pos, cnt = best
    size = {line[k]: cnt[k] for k in range(L)}
    # initial chains: the data in line order (by trap, then first use), each trap filled as far
    # as the fill limit allows without getting ahead of the plan, so that every brigade move
    # goes toward the ancilla's end of the line and nothing is ever reordered
    order = sorted(range(n), key=lambda q: (pos[q], q))
    chains: dict[str, list[str]] = {t: [] for t in line}
    done = planned = 0
    for k in range(L):
        planned += cnt[k]
        take = min(fill - 1 if k == 0 else fill, planned - done)
        part = [names[q] for q in order[done:done + take]]
        done += take
        if k == 0:
            chains[first] = part + [anc] if side0 == "R" else [anc] + part
        else:
            near = dev.via_side[(line[k], line[k - 1])]
            chains[line[k]] = part if near == "L" else part[::-1]
    if done < n:
        raise sched.SchedError("the data do not fit the line at the fill limit")
    return {"anc": anc, "line": line, "chains": chains, "size": size, "dev": dev,
            "trap_of": {names[q]: line[pos[q]] for q in range(n)},
            "walk_us": walk}


def build(circuit: Sequence[tuple[str, str]], device: PortGraph, *, law: QCCDSimParams,
          name: str = "moveless_ours", verify: bool = True, iters: int = 150_000,
          seed: int = 0) -> TimedSchedule:
    """The schedule for ``circuit`` (every CNOT on one ancilla) on ``device``: `plan`'s
    placement, the brigade that realises it, then the ancilla's walk, CNOT by CNOT in the
    circuit's order; checked before it is returned (see the module docstring)."""
    circ = [(str(a), str(b)) for a, b in circuit]
    P = plan(circ, device, law, iters=iters, seed=seed)
    anc, line, chains, size, dev = P["anc"], P["line"], P["chains"], P["size"], P["dev"]
    st = _QCCDSimBook(dev, chains)
    # -- the bucket brigade: each trap gets the data the plan gives it before the ancilla comes
    st.why = "brigade"
    for k in range(1, len(line)):
        t = line[k]
        while len(st.cp[t]) < size[t]:
            # the nearest trap further out with an ion to spare passes it along the line
            j = next((j for j in range(k + 1, len(line)) if st.cp[line[j]]), None)
            if j is None:
                raise sched.SchedError("no data left further out to fill the trap")
            for i in range(j, k, -1):
                src, dst = line[i], line[i - 1]
                leg = dev.legs[(src, dst)]
                end = 0 if leg.side_out == "L" else -1
                ion = st.cp[src][end]
                if st.leg(ion, leg, 0.0, jit=False) is None:
                    raise sched.SchedError(f"brigade move {src}->{dst} failed")
    where = {q: t for t, c in st.cp.items() for q in c}
    if any(where[q] != t for q, t in P["trap_of"].items()):
        raise sched.SchedError("the brigade did not leave every data qubit where the plan puts it")
    # -- the ancilla's walk: every CNOT in the file's order
    pos = {t: k for k, t in enumerate(line)}
    for c, tq in circ:
        d = c if tq == anc else tq
        st.why = "walk"
        while st.loc[anc] != st.loc[d]:
            here = pos[st.loc[anc]]
            nxt = line[here + (1 if pos[st.loc[d]] > here else -1)]
            if st.leg(anc, dev.legs[(line[here], nxt)], 0.0, jit=False) is None:
                raise sched.SchedError(f"ancilla cannot step {line[here]} -> {nxt}")
        st.why = None
        st.gate(c, tq)
    ions = sorted({q for g in circ for q in g})
    s = TimedSchedule(name=name, device=device,
                      chains={t: list(c) for t, c in chains.items()},
                      events=st.events(), qubits={q: q for q in ions},
                      source={"tool": "qccd.repro.sched_moveless",
                              "plan": {"line": line, "data_per_trap": [size[t] for t in line],
                                       "walk_us": P["walk_us"], "iters": iters, "seed": seed}})
    if verify:
        for prof in (QCCDSIM_GATESET, replace(QCCDSIM_GATESET, circuit_order="strict")):
            rep = check(s, prof, duration=qccdsim_duration(law), circuit=circ, strict=True)
            if not rep.ok:
                raise sched.SchedError(f"checker rejects the schedule ({prof.circuit_order}): "
                                       f"{rep.summary()['violations']} {rep.summary()['first'][:3]}")
        kinds = Counter(e.kind for e in s.events)
        if not set(kinds) <= set(sched.ALLOWED_KINDS):
            raise sched.SchedError(f"emitted {set(kinds) - set(sched.ALLOWED_KINDS)}")
    return s


def publish(out=None, codes: Sequence[str] = ("SC9_L8", "SC25_L24", "SC49_L48", "CC91_L90")) -> list:
    """Write our schedule for each benchmark on the reproduction page, as it is published:
    the device and QCCDSim parameters of the paper's own ``<code>_Moveless_1m`` run and the
    CNOTs of its benchmark file, saved as ``<code>_ours.schedule.json.gz`` in
    ``Reproduce/khan2025moveless/ours`` (or ``out``), gzip'd with no timestamp so that a
    rebuild is identical byte for byte."""
    import gzip
    import json
    from pathlib import Path

    from . import catalog, importers
    from .__main__ import ROOT, _circuit, _open

    base = ROOT / "khan2025moveless"
    out = Path(out) if out is not None else base / "ours"
    written = []
    for code in codes:
        run = f"{code}_Moveless_1m"
        path = _open(base / "artifact" / f"{run}.schedule.json.gz")
        raw = json.loads(path.read_text(encoding="utf-8"))
        law = importers.qccdsim_params(raw["config"], raw["device"])
        spec = next(r for r in catalog.paper("khan2025moveless")["runs"] if r["id"] == run)
        circ = _circuit(base, spec["circuit"])
        s = build(circ, importers.import_qccdsim(path).device, law=law, name=f"{code}_ours")
        p = out / f"{code}_ours.schedule.json.gz"
        p.write_bytes(gzip.compress(json.dumps(s.to_json(), separators=(",", ":")).encode("utf-8"),
                                    compresslevel=9, mtime=0))
        written.append(p)
    return written


if __name__ == "__main__":
    for p in publish():
        print(p)
