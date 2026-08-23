"""Layer 4 -- the objective.  PLAN §6.

Three tiers of fidelity to one scalar:

T1 combinatorial  `total_steps`, `total_cost`, rotate hops, batch utilization,
                  junction contention, cooling-op count.  Microseconds to evaluate.
T2 physical       per-ion n-bar, wall clock, cooling time, gate error from R16.
T3 logical        a `stim` circuit with the T2 parameters attached, decoded with BP-OSD.
                  Not in this milestone.
"""

from __future__ import annotations

from .combinatorial import T1Metrics, t1_metrics
from .models import (
    QUANTA_COMPONENTS,
    Charge,
    CorrectedModel,
    CostModel,
    DeckModel,
    corrected_model,
    deck_model,
)
from .physical import T2Metrics, t2_metrics

__all__ = [
    "Charge",
    "CorrectedModel",
    "CostModel",
    "DeckModel",
    "QUANTA_COMPONENTS",
    "T1Metrics",
    "T2Metrics",
    "corrected_model",
    "deck_model",
    "t1_metrics",
    "t2_metrics",
]
