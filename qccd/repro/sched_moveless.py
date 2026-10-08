"""Our schedule for Moveless's benchmarks: one ancilla, a line of traps, the CNOTs as a set.

Moveless (Khan et al. 2025) runs one syndrome round's CNOTs, every one of them on a single
ancilla, on a line of traps filled to 5 ions with 2 slots kept for traffic, under QCCDSim's
clock.  Its benchmark files put no measurement between stabilizers, so the paper's own
compiler (and the replay, profile `QCCDSIM_GATESET`) treats the CNOTs as a multiset.  With
one ancilla every CNOT is serial, so the floor is (number of CNOTs) x (gate time); what is
left to win is the ancilla's travel.

The plan here:

* the ancilla starts at the port end of the end trap with ``fill - 1`` data; every later
  trap it visits holds ``capacity - 1`` data when it arrives, so the ancilla stops in as few
  traps as the capacity allows and visits each once, left to right;
* the traps right of the ancilla start at the fill limit like every compiler's; the extra ion
  each one needs is handed leftwards by a bucket brigade (each trap passes the ion at its left
  end to its left neighbour, which takes it at its right end -- no reorder anywhere), long
  before the ancilla gets there;
* in each trap the ancilla runs every CNOT of the data it finds there, back to back; it
  enters a trap at its left end and so leaves through the right with a gate swap (QCCDSim
  prices it 3 gates + a split), the one reorder in the schedule.

Every event is built and timed by `sched._State` (trap, segment and junction reservations,
both QCCDSim chain books) and the result is replayed by `timed.check` under the paper's
profile, law and circuit with strict durations before it is returned.
"""

from __future__ import annotations

from collections import Counter
from typing import Sequence

from . import sched
from .models import QCCDSIM_GATESET, QCCDSimParams, qccdsim_duration
from .timed import PortGraph, TimedSchedule, check

__all__ = ["build", "plan"]


def _ancilla(circ: Sequence[tuple[str, str]]) -> str:
    common = set(circ[0])
    for g in circ:
        common &= set(g)
    if len(common) != 1:
        raise sched.SchedError("this plan is for circuits whose every CNOT shares one ancilla")
    return next(iter(common))


def plan(circ: Sequence[tuple[str, str]], device: PortGraph, law: QCCDSimParams) -> dict:
    """The initial chains and the batch sizes (data per trap the ancilla stops in)."""
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
    order: list[str] = []
    for a, b in circ:
        for q in (a, b):
            if q != anc and q not in order:
                order.append(q)
    fill = dev.fill[first]
    cap = dev.cap[first]
    if any(dev.fill[t] != fill or dev.cap[t] != cap for t in line):
        raise sched.SchedError("this plan assumes every trap has the same capacity and fill")
    b0 = min(fill - 1, len(order))
    rest = len(order) - b0
    sizes = [b0]
    while rest > 0:
        sizes.append(min(cap - 1, rest))
        rest -= sizes[-1]
    if len(sizes) > len(line):
        raise sched.SchedError("the line is too short for this many batches")
    # initial chains: the ancilla's trap, then every later trap at the fill limit
    chains = {t: [] for t in line}
    data0 = order[:b0]
    chains[first] = data0 + [anc] if side0 == "R" else [anc] + data0
    it = iter(order[b0:])
    k = 1
    left = len(order) - b0
    while left > 0:
        n = min(fill, left)
        chains[line[k]] = [next(it) for _ in range(n)]
        left -= n
        k += 1
    if k > len(line):
        raise sched.SchedError("the data do not fit the line at the fill limit")
    return {"anc": anc, "line": line, "chains": chains, "sizes": sizes, "dev": dev}


def build(circuit: Sequence[tuple[str, str]], device: PortGraph, *, law: QCCDSimParams,
          name: str = "moveless_ours", verify: bool = True) -> TimedSchedule:
    circ = [(str(a), str(b)) for a, b in circuit]
    P = plan(circ, device, law)
    anc, line, chains, sizes, dev = P["anc"], P["line"], P["chains"], P["sizes"], P["dev"]
    st = sched._State(dev, chains)
    # -- the bucket brigade: trap k (k >= 1) holds sizes[k] data before the ancilla comes
    want = {line[k]: sizes[k] for k in range(1, len(sizes))}
    st.why = "brigade"
    for k in range(1, len(sizes)):
        t = line[k]
        while len(st.cp[t]) < want[t]:
            # the nearest trap to the right with an ion to spare passes it along the line
            j = next((j for j in range(k + 1, len(line)) if st.cp[line[j]]), None)
            if j is None:
                raise sched.SchedError("no data left to the right to fill the batch")
            for i in range(j, k, -1):
                src, dst = line[i], line[i - 1]
                leg = dev.legs[(src, dst)]
                end = 0 if leg.side_out == "L" else -1
                ion = st.cp[src][end]
                if st.leg(ion, leg, 0.0, jit=False) is None:
                    raise sched.SchedError(f"brigade move {src}->{dst} failed")
    # -- the ancilla's tour
    by_ion: dict[str, list[tuple[str, str]]] = {}
    for g in circ:
        d = g[0] if g[1] == anc else g[1]
        by_ion.setdefault(d, []).append(g)
    for k in range(len(sizes)):
        t = line[k]
        if st.loc[anc] != t:
            raise sched.SchedError(f"ancilla is in {st.loc[anc]}, expected {t}")
        st.why = None
        here = [q for q in st.cp[t] if q != anc]
        for q in sorted(here, key=lambda q: circ.index(by_ion[q][0])):
            for c, tq in by_ion.pop(q):
                st.gate(c, tq)
        if k + 1 < len(sizes):
            st.why = "tour"
            if st.leg(anc, dev.legs[(t, line[k + 1])], 0.0, jit=False) is None:
                raise sched.SchedError(f"ancilla cannot leave {t}")
    if by_ion:
        raise sched.SchedError(f"data never visited: {sorted(by_ion)}")
    ions = sorted({q for g in circ for q in g})
    s = TimedSchedule(name=name, device=device,
                      chains={t: list(c) for t, c in chains.items()},
                      events=st.events(), qubits={q: q for q in ions},
                      source={"tool": "qccd.repro.sched_moveless",
                              "plan": {"batches": sizes, "line": line}})
    if verify:
        rep = check(s, QCCDSIM_GATESET, duration=qccdsim_duration(law), circuit=circ, strict=True)
        if not rep.ok:
            raise sched.SchedError(f"checker rejects the schedule: {rep.summary()['violations']} "
                                   f"{rep.summary()['first'][:3]}")
        kinds = Counter(e.kind for e in s.events)
        if not set(kinds) <= set(sched.ALLOWED_KINDS):
            raise sched.SchedError(f"emitted {set(kinds) - set(sched.ALLOWED_KINDS)}")
    return s
