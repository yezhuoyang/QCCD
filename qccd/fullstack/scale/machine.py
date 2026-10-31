"""The machine Gidney 2025 runs on at RSA-2048 (docs/FULLSTACK_PLAN.md, slice S9): one place for
its code distance, physics and noise.

The user, 2026-10-31: "use much smaller code distance, as the gate error rate of trapped ion should
be better than superconducting qubit -- be optimistic about future trapped ion hardware progress".
So the machine is projected, by one stated factor, from published trapped-ion numbers:

  physics  `qccd-physics-best`: the best published fidelity for each operation, each cited (two-qubit
           8.4e-5, arXiv:2510.17286; one-qubit 1.5e-7, arXiv:2412.04421; SPAM 7e-6, arXiv:2409.05805),
           every other number Helios's (qccd-physics@2)
  noise    qccd-noise@2 at scale 1/2: every error half of today's best -- the one projection
  distance 11, from `tools/distance_study.py`: Lambda = 206 at this noise (103 at today's best,
           13.7 on Helios; measured at d = 3 as 5.8e-7 a round against the fit's 5.9e-7), so
           p = 0.025 * 206^-((d + 1) / 2) = 3.3e-16 per patch per round, and Gidney's 6.9e13
           logical-qubit rounds a shot lose 2.2 % of shots (the paper allows 6.7 %)
  rounds   a merge keeps FormalRV's 25 (the user, 2026-10-29: "keep 25 to stay paper faithful")

At today's best numbers (scale 1) the same budget needs d = 13; on Helios, d = 23.
"""
from __future__ import annotations

from dataclasses import replace

__all__ = ["DISTANCE", "MERGE_ROUNDS", "PHYSICS", "NOISE_SCALE", "noise", "device"]

DISTANCE = 11
MERGE_ROUNDS = 25
PHYSICS = "best"
NOISE_SCALE = 0.5


def noise():
    """The noise the machine's gadgets are evaluated under: qccd-noise@2 at `NOISE_SCALE`."""
    from ...qec.noise import NoiseModel2
    return replace(NoiseModel2(), scale=NOISE_SCALE)


def device(rows: int, cols: int | None = None, **kw):
    """The tiled grid a rows x cols region of the machine is played on, with its physics."""
    from ...compile.tiled_surface import device as tiled_device
    return tiled_device(rows, cols, physics=PHYSICS, **kw)
