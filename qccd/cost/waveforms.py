"""The waveform census: how many different things a broadcast plane is asked to play at once.

`hardware_report` counts the DACs a device's channel map HAS.  This counts what a compiled
programme ASKS of them, cycle by cycle, so the two can be compared -- the gap an author of
the WISE/WISER accounting (arXiv:2510.23519, 2609.13546) pointed at.

What one transport cycle needs
------------------------------
A shared (broadcast, row, column) plane drives every site on a channel with one waveform,
and a motion takes every electrode role of the cell, so one motion is one channel SET.  In
one cycle a site does one of three things:

    moves      it follows the motion bank its move needs; distinct motion signatures
               (`qccd.verify.rules.motion_signature`, the unit R22 judges) need distinct
               banks
    holds      it is LOADED and no move leaves, enters or passes through it; on the deck's
               array it follows the HOLD bank, which is the other half of p.19's
               "every DC is wired through a two-way switch" (WISE's N_set = 2, "stay or
               swap": arXiv:2305.12773, Table I)
    is empty   nothing to drive; any waveform will do

So the banks one cycle needs are `len(signatures) + (1 if any site holds)`, and the plane
needs the maximum of that over the programme: `max_concurrent`.  WISE notes (fn. [46]) the
hold bank could be dropped by leaving electrodes unconnected, which is why R4d does NOT
count it and this census reports it separately (`n_cycles_with_holds`).  A switched plane
picks a site's bank with `ceil(log2(max_concurrent))` select bits per site per cycle (WISE
Table I: N_sw = log2(N_set^N)).

What happens between transport and gates
----------------------------------------
Shims (compensation electrodes) are charged one at a time through a 1:M analog demux:
`t_sc = M * t_ec`, t_ec = 3 us (WISE Sec. V B 3).  WISE re-sets them after each
reconfiguration; WISER (arXiv:2609.13546, §4.2) charges every gate layer.  The census
counts both: `n_reconfigurations` (gate-type instructions with transport since the last
one) and `n_gate_layers` (every gate, measure and reset instruction).

The census replays through `qccd.verify.replay(..., on_cycle=...)`, so it sees the SAME
`CycleView` the verifier judges, and it copies nothing per cycle but a handful of sets.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Mapping

__all__ = ["WaveformCensus", "waveform_census"]

#: instruction types that are a gate layer for the shim-charging count
GATE_TYPES = ("gate", "measure", "reset")


@dataclass(frozen=True)
class WaveformCensus:
    """Per-programme summary of what the broadcast waveforms are asked to do."""

    arch: str
    program: str
    n_transport_cycles: int = 0
    #: most distinct motion signatures in one transport cycle (the MOTION banks)
    max_signatures: int = 0
    #: most banks one cycle needs: signatures + 1 when a loaded site holds
    max_concurrent: int = 0
    #: concurrent banks -> how many transport cycles needed that many
    concurrent_histogram: Mapping[int, int] = field(default_factory=dict)
    #: distinct signatures -> how many transport cycles
    signature_histogram: Mapping[int, int] = field(default_factory=dict)
    n_cycles_with_holds: int = 0
    #: the most loaded sites holding in one cycle
    max_holds: int = 0
    min_cycle_us: float = 0.0          # shortest transport cycle with a duration
    median_cycle_us: float = 0.0
    n_gate_layers: int = 0             # gate / measure / reset instructions
    n_reconfigurations: int = 0        # gate-type instructions with transport since the last
    total_us: float = 0.0              # the replay's T, for scale

    def as_dict(self) -> dict:
        return {
            "arch": self.arch,
            "program": self.program,
            "n_transport_cycles": self.n_transport_cycles,
            "max_signatures": self.max_signatures,
            "max_concurrent": self.max_concurrent,
            "concurrent_histogram": {str(k): v for k, v in sorted(self.concurrent_histogram.items())},
            "signature_histogram": {str(k): v for k, v in sorted(self.signature_histogram.items())},
            "n_cycles_with_holds": self.n_cycles_with_holds,
            "max_holds": self.max_holds,
            "min_cycle_us": self.min_cycle_us,
            "median_cycle_us": self.median_cycle_us,
            "n_gate_layers": self.n_gate_layers,
            "n_reconfigurations": self.n_reconfigurations,
            "total_us": self.total_us,
        }


def waveform_census(prog, arch, model=None) -> WaveformCensus:
    """Replay `prog` on `arch` (rules off) and count, per transport cycle, the distinct
    motion signatures, whether a loaded site holds, and the banks that makes; plus the
    gate layers and reconfigurations the shim demultiplexer is charged for.

    `model` defaults to `qccd.cost.corrected_model()`; it sets the cycle durations, and
    the select rate in `hardware_report` divides by the shortest one.
    """
    from . import corrected_model
    from ..verify import replay
    from ..verify.rules import motion_signatures

    if model is None:
        model = corrected_model()
    sites = frozenset(n for n, node in arch.device.nodes.items() if node.kind == "site")

    concurrent: dict[int, int] = {}
    signatures: dict[int, int] = {}
    labels: dict = {}                  # hop-label memo: one device, one replay
    durations: list[float] = []
    state = {"holds": 0, "max_holds": 0, "gate": 0, "reconf": 0, "moved": False,
             "max_sig": 0, "max_con": 0}

    def on_cycle(v) -> None:
        t = v.instr.type
        if t in GATE_TYPES:
            state["gate"] += 1
            if state["moved"]:
                state["reconf"] += 1
                state["moved"] = False
            return
        if t != "simd" or not v.moves:
            return
        state["moved"] = True
        touched: set[str] = set()
        for m in v.moves:
            touched.add(m.src)
            touched.add(m.dst)
        touched.update(v.transits or ())
        k = len(set(motion_signatures(v, cache=labels).values()))
        held = sites.intersection(v.occ_before)
        held -= touched
        n_held = sum(1 for s in held if v.occ_before.get(s, 0) > 0)
        c = k + (1 if n_held else 0)
        concurrent[c] = concurrent.get(c, 0) + 1
        signatures[k] = signatures.get(k, 0) + 1
        if n_held:
            state["holds"] += 1
            if n_held > state["max_holds"]:
                state["max_holds"] = n_held
        if k > state["max_sig"]:
            state["max_sig"] = k
        if c > state["max_con"]:
            state["max_con"] = c
        durations.append(v.duration_us)

    res = replay(prog, arch, model, check_rules=False, keep_cycles=False, on_cycle=on_cycle)
    positive = [d for d in durations if d > 0]
    return WaveformCensus(
        arch=arch.name,
        program=getattr(prog, "name", ""),
        n_transport_cycles=len(durations),
        max_signatures=state["max_sig"],
        max_concurrent=state["max_con"],
        concurrent_histogram=dict(sorted(concurrent.items())),
        signature_histogram=dict(sorted(signatures.items())),
        n_cycles_with_holds=state["holds"],
        max_holds=state["max_holds"],
        min_cycle_us=min(positive) if positive else 0.0,
        median_cycle_us=float(statistics.median(durations)) if durations else 0.0,
        n_gate_layers=state["gate"],
        n_reconfigurations=state["reconf"],
        total_us=float(res.total_us),
    )
