"""Each paper's clock and its fidelity law, written down once.

Every constant here is quoted from a paper or read from its artifact; the step-0 reports
(``Reproduce/<paper>/paper.md``) give the line it came from.  Where a paper's prose and its
code disagree, the law follows the CODE, because the code is what produced the numbers we
are reproducing, and the disagreement is reported as a note on the paper -- never fixed
silently in either direction.

A duration law is a function ``(event, ctx) -> microseconds | None`` for `timed.check`;
None means the law makes no claim about that event.  A fidelity model is an observer that
sees every event with the state before it and accumulates whatever the paper accumulates.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Mapping

from .timed import Context, Event, Profile

__all__ = [
    "QCCDSIM",
    "QCCDSIM_PHYSICAL",
    "QCCDSIM_GATESET",
    "TISCC",
    "JONES",
    "QCCDSimParams",
    "qccdsim_gate_us",
    "qccdsim_duration",
    "QCCDSimAnalyzer",
    "TISCC_US",
    "tiscc_duration",
    "JONES_US",
    "jones_duration",
    "jones_duration_6d4b467",
    "CYCLONE",
    "cyclone_duration",
]


# --------------------------------------------------------------------------- QCCDSim
#
# Murali et al., ISCA 2020, and every artifact built on its code: Muzzle the Shuttle
# (Saki et al., DATE 2022), Moveless (Khan et al. 2025) and the baseline of Cyclone (Khan
# et al., HPCA 2026).

#: The rules QCCDSim's scheduler enforces (`ejf_schedule.py`, flags `1 0 0`): every gate,
#: split and merge on a trap after the trap's previous event; a whole shuttle path reserved
#: before it starts; one move through a junction at a time; chains ordered, a split taking
#: the end ion or paying for a reorder.
QCCDSIM = Profile(
    "qccdsim",
    trap_serial=("gate", "split", "merge"),
    segment_mutex=True,
    junction_mutex=True,
    chain_order="qccdsim",
    circuit_order="strict",
)

#: The same rules with a physical gate swap: the end ion's state moves to where the
#: departing ion stood.  QCCDSim leaves it at the end instead (`ejf_schedule._add_shuttle_ops`
#: removes the moving ion in place), which can spare later splits a swap they would need.
QCCDSIM_PHYSICAL = Profile(
    "qccdsim-physical-swap",
    trap_serial=QCCDSIM.trap_serial,
    segment_mutex=True,
    junction_mutex=True,
    chain_order="physical",
    circuit_order="strict",
)


#: QCCDSim's rules, with the circuit's two-qubit gates checked as a set: for benchmark files
#: that list a round's CNOTs for one ancilla with no measurement between stabilizers, which
#: a compiler may then run in any order (Moveless).
QCCDSIM_GATESET = Profile(
    "qccdsim (gates as a set)",
    trap_serial=QCCDSIM.trap_serial,
    segment_mutex=True,
    junction_mutex=True,
    chain_order="qccdsim",
    circuit_order="multiset",
)


@dataclass(frozen=True)
class QCCDSimParams:
    """`run.py`'s `mpar_model1` plus the CLI's gate and swap choices."""

    gate: str = "FM"                 # FM | Duan (AM1) | Trout (AM2) | PM
    swap: str = "GateSwap"           # GateSwap | IonSwap
    split_merge_us: float = 80
    shuttle_us: float = 5
    junction_us: Mapping[int, float] = field(default_factory=lambda: {2: 5, 3: 100, 4: 120})
    ion_swap_us: float = 42
    #: `Analyzer.honeywell_mode`: True (the code's default) starts every gate at fidelity
    #: 0.992 and adds 2 quanta per split/merge/move; the ISCA paper's plots need False
    honeywell: bool = False
    #: quanta per merge and per reordering split (k1) and per move (k2) outside Honeywell
    #: mode: 0.1 and 0.01 in QCCDSim; Muzzle the Shuttle's analyzer uses 0.01 and 0.001
    k1: float = 0.1
    k2: float = 0.01


def qccdsim_gate_us(p: QCCDSimParams, capacity: int, chain: list[str] | None,
                    a: str, b: str) -> int:
    """`Machine.gate_time`: the FM law reads the trap CAPACITY (+2 headroom), the AM/PM laws
    the distance between the two ions in the chain.  Truncated to an integer, floor 1."""
    if p.gate == "FM":
        t = max(100.0, 13.33 * capacity - 54)
    else:
        if chain is None or a not in chain or b not in chain:
            raise ValueError("the AM/PM gate laws need the chain order")
        d = abs(chain.index(a) - chain.index(b))
        t = {"Duan": -22 + 100 * d, "Trout": 10 + 38 * d, "PM": 160 + 5 * d}[p.gate]
    return int(max(t, 1))


def qccdsim_duration(p: QCCDSimParams) -> Callable[[Event, Context], float | None]:
    """`Machine.split_time / merge_time / move_time + junction_cross_time / gate_time`."""

    def law(e: Event, ctx: Context) -> float | None:
        dev = ctx.device
        if e.kind == "gate":
            cap = dev.capacity(e.at)
            # FM keys on `self.traps[0].capacity`: every trap has the same capacity here
            return qccdsim_gate_us(p, cap, ctx.chain, e.ions[0], e.ions[1])
        if e.kind == "merge":
            return int(p.split_merge_us)
        if e.kind == "move":
            via = e.via or next(iter(set(dev.segments[e.src]) & set(dev.segments[e.dst])))
            return p.shuttle_us + p.junction_us[dev.degree(via)]
        if e.kind == "split":
            sw = e.swap or {}
            if not sw:
                return int(p.split_merge_us)
            if sw.get("kind") == "gate":
                if p.swap != "GateSwap":
                    return None              # this run reorders by ion swaps, not gate swaps
                return int(3 * qccdsim_gate_us(p, dev.capacity(e.at), ctx.chain, e.ions[0],
                                               sw["with"]) + p.split_merge_us)
            if p.swap != "IonSwap":
                return None                  # this run reorders by gate swaps, not ion swaps
            h = int(sw["hops"])
            return int(h * p.split_merge_us + (h - 1) * p.split_merge_us + p.ion_swap_us * h)
        return None

    return law


class QCCDSimAnalyzer:
    """`analyzer.Analyzer.move_check`, replayed beside the checker, quirks included.

    Kept exactly because the fidelities we compare against were printed by it:

    * its chains are its OWN: a merge appends whatever port it arrives through, so an AM/PM
      gate time here can differ from the one the scheduler charged;
    * a split changes heat only when it carried a reorder;
    * a gate's motional term reads the TRAP's total quanta, not a per-ion or per-mode value;
    * fidelity is the product over two-qubit gates (a gate swap counts its gate three times)
      and nothing else: no single-qubit, measurement, idle or cooling term.
    """

    def __init__(self, p: QCCDSimParams, chains: Mapping[str, list[str]], capacity: int):
        self.p = p
        self.capacity = capacity
        self.chains = {s: list(c) for s, c in chains.items()}
        self.heat: dict[str, float] = {s: 0.0 for s in self.chains}
        self.carried: dict[str, float] = {}
        self.log_f = 0.0
        self.background: list[float] = []
        self.motional: list[float] = []

    def _fid(self, trap: str, a: str, b: str) -> float:
        n = len(self.chains[trap])
        big_a = max(0.0001, 0.0001 * n / math.log(n) - 0.00053)
        tau = qccdsim_gate_us(self.p, self.capacity, self.chains[trap], a, b)
        x1 = 1.0 * tau / 1e6
        x2 = big_a * (2 * self.heat[trap] + 1)
        self.background.append(x1)
        self.motional.append(x2)
        base = 0.992 if self.p.honeywell else 1.0
        return math.log(max(0.0001, base - x1 - x2))

    def __call__(self, e: Event, ctx: Context) -> None:
        hw = self.p.honeywell
        if e.kind == "gate":
            self.log_f += self._fid(e.at, e.ions[0], e.ions[1])
        elif e.kind == "split":
            ion, trap = e.ions[0], e.at
            m = e.meta
            n = len(self.chains[trap])
            share = self.heat[trap] / n
            ion_hops = int(m.get("ion_hops", 0) or 0)
            gate_hops = int(m.get("swap_hops", 0) or 0)
            k1, k2 = (2, 2) if hw else (self.p.k1, self.p.k2)
            if ion_hops:
                self.heat[trap] += k1 * ion_hops + k1 * (ion_hops - 1) + k2 * ion_hops
                self.carried[ion] = share + k1
            if gate_hops:
                i1, i2 = m.get("i1"), m.get("i2")
                if i1 != i2:
                    self.log_f += 3 * self._fid(trap, str(i1), str(i2))
                val = k1
                self.heat[trap] = self.heat[trap] - share + val
                self.carried[ion] = share + val
            self.chains[trap].remove(ion)
        elif e.kind == "move":
            for ion in e.ions:
                self.carried[ion] = self.carried.get(ion, 0.0) + (2 if hw else self.p.k2)
        elif e.kind == "merge":
            ion, trap = e.ions[0], e.at
            self.chains[trap].append(ion)
            self.heat[trap] += self.carried.get(ion, 0.0) + (2 if hw else self.p.k1)
            self.carried[ion] = 0.0

    @property
    def fidelity(self) -> float:
        return math.exp(self.log_f)

    @property
    def heating(self) -> float:
        return sum(self.heat.values())


# ----------------------------------------------------------------------------- TISCC
#
# LeBlond et al., SC-W 2023 (arXiv 2311.10687): the native gate table, and the code's
# junction move (`move_along_path`: Move + one Junction, 110.25 us), not the paper's "two
# Junction move operations" (210 us) -- the shipped schedules use the code's.

TISCC = Profile(
    "tiscc",
    trap_serial=(),                 # one ion per zone: the zone IS the ion
    segment_mutex=False,
    junction_mutex=True,            # "two qubits do not move through the same junction at the same time"
    # rotations, preparation and measurement happen in O zones only (TISCC's
    # GridManager: operations other than ZZ and Move are applied to O-zone qubits)
    op_zones=(("gate1", ("O",)), ("prepare", ("O",)), ("measure", ("O",))),
    chain_order=None,
    gate_locality="adjacent",       # the ZZ acts on ions in neighbouring zones of one segment
    circuit_order="multiset",
)

TISCC_US = {
    "Prepare_Z": 10.0, "Measure_Z": 120.0,
    "X_pi/2": 10.0, "X_pi/4": 10.0, "X_-pi/4": 10.0, "X_-pi/2": 10.0,
    "Y_pi/2": 10.0, "Y_pi/4": 10.0, "Y_-pi/4": 10.0, "Y_-pi/2": 10.0,
    "Z_pi/2": 3.0, "Z_pi/4": 3.0, "Z_-pi/4": 3.0, "Z_pi/8": 3.0, "Z_-pi/8": 3.0, "Z_-pi/2": 3.0,
    "ZZ": 2000.0, "Move": 5.25, "Junction": 105.0,
}


#: which of TISCC's operations each event kind may be: an event is priced by its op only
#: when the op is one its kind can be (a "gate" named Move would otherwise cost 5.25 us)
_TISCC_OPS = {"gate": {"ZZ"}, "prepare": {"Prepare_Z"}, "measure": {"Measure_Z"}, "hop": {"Move"},
              "gate1": {k for k in TISCC_US if k[:2] in ("X_", "Y_", "Z_")}}


def tiscc_duration(e: Event, ctx: Context) -> float | None:
    op = e.meta.get("op")
    if op is None or op not in _TISCC_OPS.get(e.kind, ()):
        return None
    if op == "Move":
        return TISCC_US["Move"] + (TISCC_US["Junction"] if e.path else 0.0)
    return TISCC_US.get(op)


# ----------------------------------------------------------------------------- Jones
#
# Jones et al. 2025 (arXiv 2510.23519), Table 1 as the artifact applies it: a junction
# passage is an entry and an exit of 50 us each, added to the 5 us shuttles on either side;
# a gate swap is three bare MS gates; one operation at a time per trap, single-qubit gates
# included; one ion per segment and per junction.

JONES = Profile(
    "jones",
    trap_serial=("gate", "gate1", "split", "merge", "swap", "measure", "reset"),
    segment_mutex=True,
    junction_mutex=True,
    chain_order="physical",
    circuit_order="commuting",
    declared_locks=True,             # the artifact lists every component an op holds
)

JONES_US = {"gate": 40.0, "gate1": 5.0, "measure": 400.0, "reset": 50.0, "split": 80.0,
            "merge": 80.0, "transit": 5.0, "j_enter": 50.0, "j_exit": 50.0, "swap": 120.0}


def jones_duration(e: Event, ctx: Context) -> float | None:
    return JONES_US.get(e.kind)


#: The artifact before its December 2024 change: a junction entry or exit took 100 us, not
#: 50.  Table 2's switch row (5,325 us) was produced with these times; its grid rows with
#: the current ones.
JONES_US_6D4B467 = dict(JONES_US, j_enter=100.0, j_exit=100.0)


def jones_duration_6d4b467(e: Event, ctx: Context) -> float | None:
    return JONES_US_6D4B467.get(e.kind)


# --------------------------------------------------------------------------- Cyclone
#
# Khan et al., HPCA 2026 (arXiv 2511.15910), as the artifact prices a step: FM gates keyed
# to the trap capacity (g = 100 us while capacity <= 11), a gate swap of 3g charged for every
# ancilla every step, split 80 + move 5 + merge 80 (the degree-2 junction is free: s = 165).

CYCLONE = Profile(
    "cyclone",
    trap_serial=("gate", "split", "merge"),
    segment_mutex=True,
    junction_mutex=True,
    chain_order=None,                # the compiler keeps a list per trap, not a chain
    circuit_order="commuting",       # within a phase every CX of a qubit has the same role
)


def cyclone_duration(e: Event, ctx: Context) -> float | None:
    if e.kind == "gate":
        return max(100.0, 13.33 * ctx.device.capacity(e.at) - 54)
    if e.kind == "split":
        return 80.0 + float(e.meta.get("swap_us", 0.0))
    if e.kind == "move":
        return 5.0
    if e.kind == "merge":
        return 80.0
    if e.kind == "gate1":
        return 100.0
    return None
