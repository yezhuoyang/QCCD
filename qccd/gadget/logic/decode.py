"""What a decoder is, and what it means for one to be correct.  GADGETS.md §10.

A decoder is a classical function: syndrome bits in, a correction out.  In this layer a
correction is never a pulse on an ion -- it is **an update to a logical block's Pauli
frame**, which is what sliding-window decoders in the literature emit (IBM's real-time
window decoder, 2510.21600: "the output of the decoder is an update to the logical Pauli
frame"), and what the classical memory place then holds.

The decoder is verified against exactly the same experiment the gadget's fault distance
was measured in (`experiment.build_experiment`): detectors are the deterministic record
parities that carry no logical information, observables the parities that do.  For a
distance-3 gadget a **lookup table** is enough, and building it is the proof:

    for every single fault, compute (detector signature, observable flips)
    group the faults by signature
    * every signature maps to ONE observable-flip pattern  -> the table corrects
      every single fault, and the correction is that pattern
    * the all-zero signature must map to no flip           -> no undetectable single fault
    the table is the whole function: syndrome -> frame update

`lookup_table` returns that table with the two checks and the numbers a schedule needs:
how many syndrome bits the table reads, how many entries it has, and (from the circuit's
own instruction times) the **reaction time** -- the last measurement the decision waits
for, plus the link and lookup latencies the place model charges.
"""

from __future__ import annotations

from .faults import propagate

__all__ = ["lookup_table", "LATENCY"]

#: The latencies the place model charges, each from a measured system in the literature.
LATENCY = {
    "link_us": 5.0,
    "link_note": "measurement to a conditional branch on the control FPGA: 5 µs on a "
                 "trapped-ion QCCD machine (AQT M-ACTION, 2101.11390)",
    "lookup_us": 1.0,
    "lookup_note": "one table read for a distance-3 window; an FPGA belief-propagation "
                   "iteration is 24 ns (IBM real-time decoder, 2510.21600), so a lookup "
                   "of this size is sub-µs -- 1 µs is charged",
    "write_us": 0.1,
    "write_note": "a frame update or an outcome written into the classical memory",
    "resolve_us": 0.2,
    "resolve_note": "reading the frame back to answer one question (a guard, a basis)",
    "decision_us": 2.0,
    "decision_note": "the guard travelling from the memory to the place that waits for it",
}


def lookup_table(exp, *, max_entries: int = 4096) -> dict:
    """The decoder for one gadget experiment, and the proof that it corrects every single
    fault.  `exp` is an `experiment.Experiment`."""
    circuit, detectors, observables = exp.circuit, exp.detectors, exp.observables
    prop = propagate(circuit)
    n_faults = len(prop.faults)
    flips = [0] * n_faults                       # record flips per fault, as a bitset
    for r, fl in enumerate(prop.flips):
        f = fl
        while f:
            b = (f & -f).bit_length() - 1
            flips[b] |= 1 << r
            f ^= 1 << b

    def parity(mask: int, f: int) -> int:
        return (mask & flips[f]).bit_count() & 1

    table: dict[int, int] = {}
    clashes: list[str] = []
    for f in range(n_faults):
        syn = 0
        for i, d in enumerate(detectors):
            if parity(d, f):
                syn |= 1 << i
        cor = 0
        for i, o in enumerate(observables):
            if parity(o, f):
                cor |= 1 << i
        if syn in table and table[syn] != cor:
            if len(clashes) < 4:
                clashes.append(f"{prop.faults[f].describe(circuit)} and an earlier fault "
                               f"give the same syndrome but different logical effects")
            continue
        table[syn] = cor
    silent = table.get(0, 0)
    report = {
        "kind": "lookup",
        "syndrome_bits": len(detectors),
        "corrections": len(observables),
        "entries": len(table),
        "single_faults": n_faults,
        "detected": sum(1 for f in range(n_faults)
                        if any(parity(d, f) for d in detectors)),
        "clashes": clashes,
        "table": ([[s, c] for s, c in sorted(table.items())]
                  if len(table) <= max_entries else None),
    }
    report["passed"] = not clashes and silent == 0
    report["claim"] = (
        f"a {len(detectors)}-bit lookup table with {len(table)} entries corrects every one "
        f"of the {n_faults} single faults: each syndrome has one logical effect"
        if report["passed"] else
        "the table is not a function: two single faults share a syndrome and differ in "
        "their logical effect" if clashes else
        "a fault with no syndrome at all flips a logical observable")
    return report


def reaction_time(op_duration_us: float, last_measurement_us: float, *,
                  latency: dict | None = None) -> dict:
    """When a decision that depends on an op's measurements can be acted on.

    The reaction time of the literature (Walking Cat, 2604.19481) is the interval from the
    last measurement a decision depends on to the decoded result being available.  Here it
    is the wire to the decoder, the lookup, the write into the memory, and the wire that
    carries the guard back."""
    L = dict(LATENCY, **(latency or {}))
    out = L["link_us"] + L["lookup_us"] + L["write_us"]
    return {
        "last_measurement_us": round(last_measurement_us, 3),
        "op_ends_us": round(op_duration_us, 3),
        "decoded_us": round(last_measurement_us + out, 3),
        "guard_ready_us": round(last_measurement_us + out + L["resolve_us"]
                                + L["decision_us"], 3),
        "reaction_us": round(out, 3),
        "slack_us": round(op_duration_us - last_measurement_us, 3),
        "note": "the decision is ready this long after the last measurement it depends on; "
                "if that is less than the time the ions spend leaving the place, the "
                "decoder costs the schedule nothing",
    }
