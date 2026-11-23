"""Cyclone: one ring of traps, the data standing still and the ancillas rotating in lockstep.

Khan, Anand, Brown and Baker, arXiv:2511.15910, as the team's transport deck
("QCCD Transport Architectures", part 3) writes it for QCCD Studio: choose the code, sweep the
traps x and the ions per trap n <= 30, keep the rest at the paper's defaults.

The ring is `ring(x/2 + 2, 2, 0, corners="junction")`: x traps on its long sides and an L-shaped
junction at each of its four corners, of degree 2 (deck S1, HW2).  A rectangle has an even number
of sides' traps, so x is even.

**The time model is machine numbers** (deck T1-T4), so the replay needs nothing new:

* the rail between two traps is the move, 10 us, and a corner is two rails, the move plus the
  degree-2 crossing, 10 us more (T1, T2);
* a split is 80 us and carries the GateSwap that brings the ancilla to the trap's edge, 3 CX (S7,
  T3).  A trap's ancillas leave in turn, so a rotation pays A x (s + t) where T5 prints s + A x t.
  Every trap of a lockstep step waits for the slowest, so the step's time always carries it.  Its
  error, 3 x the two-qubit gate's (the team's feedback F1), lands on the ancilla and the ion it
  changes places with, where the chain order (`qccd.verify.chains`) says there is one: the noise
  model's `gate_swap` channel;
* a gate's time grows with the trap's capacity by the QCCDSim law the deck's T4 names.  The noise
  model's 70 us is its flat part: the law's 100 us at up to 12 ions is scaled to it.

**Every trap holds the same number of ions** (rule Y5; the team's comment on Goal 3's first pages,
Ke, 2026-11-23): one set of waveforms is broadcast to every trap, so each holds the same number of
data qubits and the same number of ancillas.  Where a code's qubits do not divide among the traps,
spare ions fill them (`spares`): a spare among the data stands still, and a spare in an ancilla's
place rotates with the ancillas, paying the same gate swap to reach its trap's edge.  A sweep
therefore prefers rings whose trap count divides the ancillas, which need no rotating spare.

**Heat and cooling are the other families'** (the team's feedback Q2: S10's free cooling is a paper
assumption, not hardware): the noise model's 2 quanta a split, 0.1 a rail and 3 quanta a second in
the background, and the same 3 ms cooling, which the cooling pass adds before any batch of gates
over the budget (R7).  A corner pays its two rails.
"""

from __future__ import annotations

import copy
import math
from typing import Mapping

__all__ = ["MAX_IONS_PER_TRAP", "SPLIT_US", "MERGE_US", "MOVE_US", "chain_gate_us", "device_doc",
           "primitives", "census", "traps_for", "is_cyclone", "fits", "spares"]

MAX_IONS_PER_TRAP = 30               # deck HW1
SPLIT_US, MOVE_US, MERGE_US = 80.0, 10.0, 80.0   # deck T1
_DECK = "QCCD Transport Architectures deck, Cyclone"


def chain_gate_us(n: int, flat_us: float = 70.0) -> float:
    """The gate's time in a trap of `n` ions (deck T4: "flat up to 12 ions, grows fast above ~15").
    The law is QCCDSim's as Cyclone's artifact keys it to trap capacity, max(100, 13.33 n - 54),
    and its flat part is scaled to the noise model's native gate (`flat_us`)."""
    return flat_us * max(100.0, 13.33 * n - 54.0) / 100.0


def fits(data: int, ancillas: int, x: int, n: int) -> bool:
    """Does a ring of x traps of n ions hold them, each trap carrying its data and the most
    ancillas any trap carries?  (Deck: x n >= N + m/2, with the code's ancillas here.)"""
    return (x >= 2 and x % 2 == 0 and n <= MAX_IONS_PER_TRAP
            and math.ceil(data / x) + math.ceil(ancillas / x) <= n)


def spares(data: int, ancillas: int, x: int) -> tuple[int, int]:
    """The spare ions a ring of x traps needs so every trap holds the same number (Y5): how many
    stand among the data, and how many rotate in an ancilla's place."""
    return x * math.ceil(data / x) - data, x * math.ceil(ancillas / x) - ancillas


def traps_for(data: int, ancillas: int) -> list[tuple[int, int]]:
    """The (x, n) a code is swept at: from the smallest ring whose traps stay within 30 ions up
    to a trap for every ancilla, or for every data qubit if there are more of those, each at the
    least n that fits (a larger n only slows the gates, T4).  Six rings at most, evenly spread
    on a logarithmic scale, the two ends always among them.  Every trap holds n ions (Y5), so
    the rings are those whose trap count divides the ancillas, which need no rotating spare;
    a code with no such ring in range (an odd number of ancillas with no even divisor) takes any
    even count, and its spares rotate."""
    total = data + ancillas
    if total <= 0:
        return []
    low = 2
    while not fits(data, ancillas, low, MAX_IONS_PER_TRAP):
        low += 2
    high = max(data, ancillas)
    high += high % 2
    high = max(high, low)
    evens = list(range(low, high + 1, 2))
    pool = [x for x in evens if ancillas and ancillas % x == 0] or evens
    lo, hi = pool[0], pool[-1]
    want = [lo * (hi / lo) ** (i / 5) for i in range(6)]
    xs = sorted({min(pool, key=lambda x: (abs(math.log(x / v)), x)) for v in want})
    return [(x, math.ceil(data / x) + math.ceil(ancillas / x)) for x in xs]


def device_doc(x: int, n: int, *, name: str | None = None) -> dict:
    """The ring as a machine document, before any physics: x traps of capacity n."""
    from ..api import Machine
    if x < 2 or x % 2:
        raise ValueError(f"a ring has an even number of traps, not {x}")
    if n > MAX_IONS_PER_TRAP:
        raise ValueError(f"a Cyclone trap holds at most {MAX_IONS_PER_TRAP} ions, not {n}")
    # a rectangle two sites high: x/2 traps on each long side, and a width of at least 3
    m = Machine.ring(max(x // 2 + 2, 3), 2, 0, site_zone="trap", corners="junction", name=name or f"cyclone{x}_n{n}")
    doc = copy.deepcopy(m.arch.to_json())
    geo = doc["geometry"]
    geo["params"]["family"] = "cyclone"
    geo["params"]["ions_per_trap"] = n
    doc["zone_types"]["trap"] = {**doc["zone_types"]["trap"], "capacity": n}
    for node in geo.get("nodes", ()):
        if node.get("kind") == "site":
            node["capacity"] = n
    ctl = doc.setdefault("control", {})
    classes = ctl.setdefault("classes", {})
    classes["extra"] = list(classes.get("extra", [])) + [
        {"id": "cyclone_shift", "type": "shift", "orbit": "L0", "delta": 1,
         "note": "every ancilla splits, moves and merges into the next trap, one way round, in "
                 "lockstep (deck S3, S4)"}]
    ctl["max_simd_classes_per_cycle"] = 1
    ctl["wiring"] = {"scheme": "broadcast_groups", "dacs_dynamic": 30, "electrodes_per_trap": 10,
                     "electrodes_per_junction": 20, "compensation_electrodes_per_trap": 0,
                     "note": f"{_DECK} HW4: one DAC per electrode of one linear zone and one corner "
                             "junction, each broadcast to every copy"}
    doc["description"] = (f"Cyclone: a ring of {x} traps of up to {n} ions, L-shaped junctions at its "
                          "four corners; the data stands still and the ancillas rotate in lockstep")
    return doc


def primitives(base: Mapping, n: int) -> dict:
    """The deck's prices on `base` (qccd-physics@3's primitives), for a trap of `n` ions."""
    out = copy.deepcopy(dict(base))
    point = lambda key: out[key]["curve"][0]                     # noqa: E731
    table = point("split")["table"]
    split_q, merge_q, rail_q = (float(point(k)["quanta"]) for k in ("split", "merge", "shuttle_segment"))
    g = chain_gate_us(n, float(out["ms_gate"]["us"]))
    swaps = 3
    out["ms_gate"]["us"] = g
    out["ms_gate"]["note"] = f"{out['ms_gate']['note']}. In a trap of {n}: deck T4's law, scaled to 70 us"
    noise = "Helios Nominal Noise Model Sec. 5"
    out["split"] = {"curve": [{"us": SPLIT_US + swaps * g, "quanta": split_q, "table": table,
                               "label": "split 80 us, carrying its ancilla's GateSwap, 3 CX; the noise model's split "
                                        "heat",
                               "source": f"{_DECK} T1, T3; S7; {noise}"}]}
    out["merge"] = {"curve": [{"us": MERGE_US, "quanta": merge_q, "table": table,
                               "label": "merge, 80 us; the noise model's heat (grouped with the split)",
                               "source": f"{_DECK} T1; {noise}"}]}
    out["shuttle_segment"] = {"curve": [{"us": MOVE_US, "quanta": rail_q, "table": table,
                                         "label": "the move between traps, 10 us; a corner is two rails: "
                                                  "the move and its degree-2 crossing. The noise model's shuttle heat",
                                         "source": f"{_DECK} T1, T2; {noise}"}]}
    out["junction_cross"] = {"curve_by_degree": {
        d: [{"us": us, "quanta": 0.0, "table": table, "label": f"degree {d}; Cyclone uses only degree 2",
             "source": f"{_DECK} T2"}] for d, us in (("3", 100.0), ("4", 120.0))}}
    out["cool"] = {**out["cool"],
                   "note": f"{out['cool'].get('note', '')}. Cyclone cools as the other families do (feedback Q2: "
                           "S10's free cooling is a paper assumption)".strip(". ")}
    return out


def is_cyclone(doc: Mapping) -> bool:
    return ((doc.get("geometry") or {}).get("params") or {}).get("family") == "cyclone"


def census(data: int, stabilizers: int, x: int, n: int, *, ancillas: int | None = None,
           rotations: int = 2) -> dict:
    """The deck's hardware table for one ring.  `ancillas` defaults to the deck's base form, one
    ancilla per two stabilizers; Goal 3's circuits keep one per check, so they pass their own."""
    a = stabilizers // 2 if ancillas is None else ancillas
    g = chain_gate_us(n)
    s = SPLIT_US + MOVE_US + MERGE_US
    t5 = 2 * x * (s + math.ceil(stabilizers / x) * (3 * g + g * math.ceil(x * data / stabilizers))) if stabilizers else 0
    spare_data, spare_anc = spares(data, a, x)
    return {"data_qubits": data, "stabilizers": stabilizers, "ancillas": a, "traps": x, "ions_per_trap": n,
            "total_ions": data + a, "spare_ions": spare_data + spare_anc, "spare_ions_rotating": spare_anc,
            "ions_loaded": x * (math.ceil(data / x) + math.ceil(a / x)),
            "smallest_ring_at_30": math.ceil((data + a) / MAX_IONS_PER_TRAP),
            "shuttling_paths": x, "l_junctions": 4, "dc_electrodes": 10 * x + 20 * 4, "dacs": 10 + 20,
            "moves_per_ancilla_per_round": rotations * x, "laser_pairs": n * x,
            "t5_worst_case_ms": t5 / 1000.0}
