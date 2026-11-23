"""Shuttling speed: what carrying an ion along a rail costs, from the speed it is carried at.

A machine's physics prices a shuttle as one operating point, a time and a heat per rail, whatever
the rail's length (qccd-physics@3: 5 us and 0.1 quanta, the Helios Nominal Noise Model's example
clock).  That says nothing about how fast the ion moves: on a drawing whose trap pitch is 464 um
(the default technology), 5 us a rail is 93 m/s.  This module prices a rail from a speed instead.

**Time.**  A rail of length L carried at speed v takes L / v (1 m/s is 1 um/us).

**Heat.**  Starting and stopping an ion kicks its axial motion, and the kick's amplitude grows
with the speed, so the quanta left behind grow as its square:

    quanta(v) = 0.36 * (v / 35 m/s)^2        per shuttle, whatever the rail's length

anchored on the fastest shuttle measured with sub-quantum excitation (Sterk et al., npj Quantum
Inf. 8, 68 (2022), arXiv:2201.07358: 210 um one way in 6 us, 35 m/s, at most 0.36 +- 0.08 quanta
gained, with closed-loop optimised waveforms).  At 4 m/s it gives 0.005 quanta a rail, which is
what Quantinuum measures for a Ba-Yb crystal there: 0.013 to 0.030 quanta for a round trip of four
375 um legs through a junction, of which the background heating over the trip's 375 us (29
quanta/s, not subtracted in the paper) is 0.011 (Burton et al., Phys. Rev. Lett. 130, 173202
(2023), arXiv:2206.11888, Table II and Fig. 3).  The same paper finds the excitation negligible
up to 6 m/s through a junction and coherent excitation above it, so a junction keeps the time its
own source gives it (100 to 120 us in qccd-physics@3, about 4 m/s over one pitch) and this law
covers straight rails only.  It is a simulation assumption, not a measurement of any one machine:
a real waveform's excitation is the Fourier weight of its acceleration at the trap frequency, and
can be far below or above a kick's.

**What speed then changes.**  No noise model here gives transport an error of its own.  Speed
reaches the logical error rate two ways, pulling against each other: a slower rail is longer idle
time on every ion that waits through it (the noise model's dephasing and idle leakage, and the
background heating, which accrues with time), and a faster rail is more heat for the next
two-ion gate to meet, or one more cooling before it (rule R7).

`at_speed` rewrites a machine's `shuttle_segment` point for one trap pitch; everything that
prices a programme (the replay, the cooling pass, the page) reads that point, so they stay
together.  Without it, nothing changes.
"""

from __future__ import annotations

import copy
from typing import Mapping

__all__ = ["KICK_QUANTA", "KICK_M_S", "JUNCTION_M_S", "pitch_um", "rail_us", "rail_quanta", "implied_m_s",
           "at_speed", "SOURCE"]

KICK_QUANTA, KICK_M_S = 0.36, 35.0       # arXiv:2201.07358: 35 m/s, at most 0.36 +- 0.08 quanta
JUNCTION_M_S = 6.0                       # arXiv:2206.11888: negligible excitation up to 6 m/s through a junction
SOURCE = ("speed law: time = length / speed; heat = 0.36 (v / 35 m/s)^2 a shuttle, arXiv:2201.07358, "
          "consistent with arXiv:2206.11888 at 4 m/s")


def pitch_um(tech=None) -> float:
    """One lattice unit of a drawing in micrometres: the technology's trap pitch (464 um on the
    default one, which every Goal 3 page is drawn in)."""
    from ..phys.tech import Technology, load_technology
    t = tech if isinstance(tech, Technology) else load_technology(tech or "surface_default")
    return t.nm_per_unit_x.nm / 1000.0


def rail_us(length_um: float, speed_m_s: float) -> float:
    """The time to carry an ion `length_um` at `speed_m_s`."""
    if speed_m_s <= 0:
        raise ValueError(f"a shuttling speed is positive, not {speed_m_s}")
    return float(length_um) / float(speed_m_s)


def rail_quanta(speed_m_s: float) -> float:
    """The quanta one shuttle at `speed_m_s` leaves in the ion's axial motion."""
    return KICK_QUANTA * (float(speed_m_s) / KICK_M_S) ** 2


def implied_m_s(us: float, length_um: float | None = None) -> float:
    """The speed a fixed time per rail means on a rail of `length_um` (default: one pitch)."""
    return (pitch_um() if length_um is None else float(length_um)) / float(us)


def at_speed(primitives: Mapping, speed_m_s: float, *, pitch: float | None = None) -> dict:
    """`primitives` with every straight rail carried at `speed_m_s`: the `shuttle_segment` point
    becomes one pitch at that speed, with the kick's heat.  A WISE chip's swap between neighbours
    (`wise_swap.us_by_kind.h`) is two such rails, as the deck's t0 is twice its half.  Junction
    crossings, splits, merges, and the steps a machine prices whole (a C2LR swap-or-stay, a WISE
    junction step) keep their own sources' times: they are not a straight carry at one speed."""
    L = pitch_um() if pitch is None else float(pitch)
    us, quanta = rail_us(L, speed_m_s), rail_quanta(speed_m_s)
    out = copy.deepcopy(dict(primitives))
    old = (out.get("shuttle_segment") or {}).get("curve") or [{}]
    table = old[0].get("table", "helios")
    out["shuttle_segment"] = {"curve": [{
        "us": us, "quanta": quanta, "table": table,
        "label": f"shuttle one rail: {L:g} um at {speed_m_s:g} m/s, {us:.4g} us, {quanta:.3g} quanta",
        "source": SOURCE}]}
    if "wise_swap" in out:
        by = dict(out["wise_swap"].get("us_by_kind") or {})
        by["h"] = 2 * us
        out["wise_swap"] = {**out["wise_swap"], "us_by_kind": by,
                            "note": f"{out['wise_swap'].get('note', '')}. At {speed_m_s:g} m/s a swap between "
                                    f"neighbours is two rails, {2 * us:.4g} us".strip(". ")}
    return out
