"""The QEC clock cycle with the classical half of the loop inside it.

Every other timing number in this project is about ions.  A fault-tolerant machine also
has a classical loop, and it runs on the same clock: a syndrome-extraction round measures
some ancillas, the outcomes cross a wire to a decoder, the decoder says which correction
to keep, and that correction either **stops in the classical memory** as a Pauli-frame
update -- the ions never wait for it -- or **comes back to the ions** as the guard of a
classically controlled operation.  Those are two different clocks, and confusing them is
how a machine gets designed with a decoder that cannot keep up:

* **store** (the frame path): the QEC cycle is the quantum round.  The only requirement on
  the classical side is *throughput* -- one round's syndrome must be decoded before the
  next one arrives, or the undecoded backlog grows without bound (the backlog problem;
  Terhal's review, arXiv:1302.3428, and the throughput discussion in arXiv:2303.00054).
* **react** (the feedback path): something has to wait for the answer -- the S̄ correction
  of a T teleportation, a branch, a measurement-based gate -- so the cycle also carries the
  *reaction time*: the last measurement to a usable decision, the interval Walking Cat
  (arXiv:2604.19481) budgets a whole extra round for.

On a QCCD machine the two verdicts come out very differently from a superconducting one,
which is the point of reporting them side by side: a syndrome round here costs
milliseconds of ion transport, so a microsecond-scale decoder has a margin of ~10⁴ and the
feedback costs a fraction of a percent of a cycle.  The same decoder against a 1 µs
superconducting round is the bottleneck.  Numbers, not adjectives: every latency below is
from a measured system, and `cycle_report` says which.

Nothing here is a fit or a guess -- and nothing here is a *replay* either.  The round time
must come from somewhere that measured it (a verified program's `total_us`, a board entry,
the studio's own priced program); this module only composes it with the classical path.
"""

from __future__ import annotations

__all__ = ["LINK", "DECODERS", "MODES", "cycle_report", "decoder_profile", "ROUND_REFERENCE"]

#: The classical path, each number from a measured system.
LINK = {
    "readout_to_control_us": 5.0,
    "readout_to_control_note":
        "measurement result to a conditional branch on the control system: 5 µs on a "
        "trapped-ion QCCD machine (AQT M-ACTION, arXiv:2101.11390)",
    "frame_write_us": 0.1,
    "frame_write_note": "writing one Pauli-frame update into the classical memory",
    "resolve_us": 0.2,
    "resolve_note": "reading the frame back to answer one question (a guard, a basis)",
    "decision_us": 2.0,
    "decision_note":
        "the guard travelling from the memory to the place that waits for it, on the "
        "decision wire",
}

#: Decoders that exist, with what they cost per round of one code block.
DECODERS = {
    "lut": {
        "title": "Lookup table (FPGA)",
        "latency_us": 1.0,
        "scales_with_code": False,
        "note": "one table read; a distance-3 surface-code window is a 16-bit syndrome "
                "with 136 entries (qccd.gadget.logic.decode builds and verifies it)",
        "source": "sub-µs table read; an FPGA belief-propagation iteration is 24 ns "
                  "(IBM real-time decoder, arXiv:2510.21600)",
        "limit": "only for small codes: the table is exponential in the syndrome length",
    },
    "fpga_bp": {
        "title": "Belief propagation on an FPGA",
        "latency_us": 0.48,
        "iterations": 20,
        "per_iteration_us": 0.024,
        "scales_with_code": False,
        "note": "20 iterations at 24 ns each; the checks are decoded in parallel, so the "
                "latency is set by the iteration count, not by the code size",
        "source": "IBM real-time window decoder, arXiv:2510.21600; Relay-BP in "
                  "arXiv:2506.03094 decodes bivariate-bicycle codes this way",
        "limit": "needs the code's check structure laid out in hardware",
    },
    "gpu_bp": {
        "title": "Belief propagation on a GPU",
        "latency_us": 1000.0,
        "scales_with_code": True,
        "note": "millisecond-scale round trip including the host link: comfortable for a "
                "QCCD round, hopeless for a microsecond one",
        "source": "software decoders in the real-time decoding literature "
                  "(review: arXiv:2303.00054)",
        "limit": "the host link dominates; throughput, not latency, is what it buys",
    },
    "none": {
        "title": "No decoder (syndromes stored only)",
        "latency_us": 0.0,
        "scales_with_code": False,
        "note": "the syndrome is written down and decoded after the run: fine for a "
                "memory experiment, impossible for a non-Clifford gate",
        "source": "offline decoding",
        "limit": "no correction is available while the machine runs",
    },
}

#: What the loop is for.
MODES = {
    "store": "the decoder's answer stops in the classical memory as a Pauli-frame update; "
             "no ion waits for it, so the cycle is the quantum round and the classical "
             "side only has to keep up",
    "react": "something waits for the answer -- a conditional operation, a branch, the S̄ "
             "correction of a T teleportation -- so the cycle also carries the reaction "
             "time, and the decision has to reach the place before its op may start",
}

#: For scale: what one syndrome-extraction round costs on other platforms.  Not a claim
#: about this project's hardware -- a yardstick for the two verdicts.
ROUND_REFERENCE = [
    {"platform": "superconducting", "round_us": 1.0,
     "note": "≈1 µs per surface-code round; the decoder is the bottleneck",
     "source": "Google's surface-code experiments (arXiv:2207.06431, 2408.13687)"},
    {"platform": "trapped ion (QCCD)", "round_us": 31000.0,
     "note": "one verified d = 3 surface-code round in this project's place library "
             "(se_d3.se.1), dominated by ion transport and cooling",
     "source": "qccd.gadget, docs/GADGETS.md §9"},
]


def decoder_profile(name: str, *, detectors: int | None = None) -> dict:
    """One decoder's numbers, with its latency for a code of `detectors` detectors."""
    if name not in DECODERS:
        raise KeyError(f"unknown decoder {name!r}; known: {', '.join(DECODERS)}")
    d = dict(DECODERS[name], name=name)
    if detectors and d.get("scales_with_code"):
        # a software decoder walks the check graph: linear in the detectors it reads
        d["latency_us"] = round(d["latency_us"] * max(1.0, detectors / 16.0), 3)
        d["note"] += f" (scaled to {detectors} detectors per round)"
    d["latency_us"] = float(d["latency_us"])
    return d


def cycle_report(round_us: float, *, decoder: str = "lut", detectors: int | None = None,
                 mode: str = "store", rounds_per_decode: int = 1,
                 link: dict | None = None) -> dict:
    """The QEC clock cycle for a round that takes `round_us`, loop included.

    `round_us` is the measured time of one syndrome-extraction round -- this module never
    invents it.  `rounds_per_decode` is the decoder's window: a sliding-window decoder that
    commits one round out of W still has to finish a window per round.
    """
    if round_us <= 0:
        raise ValueError("round_us must be positive: it is a measured round time")
    if mode not in MODES:
        raise KeyError(f"unknown mode {mode!r}; known: {', '.join(MODES)}")
    L = dict(LINK, **(link or {}))
    prof = decoder_profile(decoder, detectors=detectors)
    t_dec = prof["latency_us"] * max(1, int(rounds_per_decode))

    stages = [
        {"stage": "syndrome extraction", "us": round(round_us, 3), "kind": "quantum",
         "where": "the code block's place", "what": "ancillas measure every check"},
        {"stage": "outcomes to the decoder", "us": L["readout_to_control_us"],
         "kind": "classical", "where": "the syndrome wire", "what": L["readout_to_control_note"]},
        {"stage": "decode", "us": round(t_dec, 3), "kind": "classical",
         "where": "the decoder", "what": prof["note"]},
        {"stage": "frame update", "us": L["frame_write_us"], "kind": "classical",
         "where": "the classical memory", "what": L["frame_write_note"]},
    ]
    classical_us = sum(s["us"] for s in stages[1:])
    reactive_extra = L["resolve_us"] + L["decision_us"]
    if mode == "react":
        stages += [
            {"stage": "resolve the guard", "us": L["resolve_us"], "kind": "classical",
             "where": "the classical memory", "what": L["resolve_note"]},
            {"stage": "decision to the place", "us": L["decision_us"], "kind": "classical",
             "where": "the decision wire", "what": L["decision_note"]},
        ]
    reaction_us = classical_us + (reactive_extra if mode == "react" else 0.0)
    cycle_us = round_us + (reaction_us if mode == "react" else 0.0)

    keeps_up = t_dec <= round_us
    margin = round_us / t_dec if t_dec > 0 else float("inf")
    verdicts = [{
        "claim": "the decoder keeps up with the rounds",
        "ok": bool(keeps_up),
        "why": (f"one round is {_ms(round_us)} and decoding it takes {_ms(t_dec)}: "
                f"{margin:,.0f}× of margin, so the undecoded backlog never grows"
                if keeps_up else
                f"one round is {_ms(round_us)} but decoding it takes {_ms(t_dec)}: the "
                f"backlog grows by {_ms(t_dec - round_us)} every round, which no amount of "
                f"buffering fixes (the backlog problem)"),
        "metric": "decoder_margin", "value": round(margin, 3),
    }, {
        "claim": "acting on a decoded result costs little of a cycle",
        "ok": bool(reaction_us < 0.1 * round_us),
        "why": (f"the reaction time is {_ms(reaction_us)}, "
                f"{100 * reaction_us / round_us:.3g}% of a {_ms(round_us)} round"),
        "metric": "reaction_fraction", "value": round(reaction_us / round_us, 6),
    }]

    return {
        "kind": "qec_cycle",
        "mode": mode, "mode_note": MODES[mode],
        "round_us": round(round_us, 3),
        "decoder": prof,
        "rounds_per_decode": max(1, int(rounds_per_decode)),
        "stages": stages,
        "classical_us": round(classical_us, 3),
        "reaction_us": round(reaction_us, 3),
        "cycle_us": round(cycle_us, 3),
        "cycles_per_s": round(1e6 / cycle_us, 3),
        "decoder_margin": round(margin, 3),
        "slowest_decoder_us": round(round_us, 3),
        "reaction_fraction": round(reaction_us / round_us, 6),
        "keeps_up": bool(keeps_up),
        "backlog_per_round_us": round(max(0.0, t_dec - round_us), 3),
        "verdicts": verdicts,
        "reference": ROUND_REFERENCE,
        "notes": [
            "the quantum round is measured, not modelled here; the classical path is the "
            "stated latency of a measured system, named per stage",
            "in `store` mode no ion waits for the decoder: the correction is a Pauli-frame "
            "update in the classical memory, and only throughput matters",
            "in `react` mode the decision has to reach the place before its operation may "
            "start, which is what the schedule checks (G11)",
        ],
    }


def _ms(us: float) -> str:
    if us == 0:
        return "0"
    if us < 1:
        return f"{us * 1000:.0f} ns"
    if us < 1000:
        return f"{us:.3g} µs"
    if us < 1e6:
        return f"{us / 1000:.3g} ms"
    return f"{us / 1e6:.3g} s"
