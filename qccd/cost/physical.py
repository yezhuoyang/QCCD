"""T2 -- the physical tier of the objective.  PLAN §6.

Replay with the primitive curves and the anomalous-heating rate, then report

    per-ion n-bar by named component      (PLAN §0.4's budget, itemized)
    wall clock by named component          (rotation / docking / gates / cooling)
    -ln F  ~  sum_gates eps(n-bar) + n T_exe / T_coh + sum_spam eps_spam

The itemization is the point.  "1747 quanta" is not a checkable claim; "267 shuttling +
1336 junction + 144 dock/undock" is, and each of the three falls out of a different part
of the graph.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import mean
from typing import Mapping, Sequence

from ..arch import Architecture
from ..cost.models import QUANTA_COMPONENTS, CostModel
from ..verify.replay import ReplayResult

__all__ = ["T2Metrics", "t2_metrics"]


@dataclass
class T2Metrics:
    runtime_us: float
    us_by_type: dict[str, float] = field(default_factory=dict)
    us_by_class: dict[str, float] = field(default_factory=dict)
    cooling_us: float = 0.0
    quanta_per_data_ion: dict[str, float] = field(default_factory=dict)
    quanta_per_data_ion_total: float = 0.0
    quanta_per_data_ion_min: float = 0.0
    quanta_per_data_ion_max: float = 0.0
    junction_transits_per_data_ion: float = 0.0
    junction_transits_min: int = 0
    junction_transits_max: int = 0
    peak_quanta: float = 0.0
    peak_quanta_ion: str | None = None
    n_gate_pairs: int = 0
    mean_gate_quanta: float = 0.0
    max_gate_quanta_seen: float = 0.0
    gate_error_sum: float = 0.0
    idle_error: float = 0.0
    spam_error: float = 0.0
    neg_log_fidelity: float = 0.0
    counterfactual_rotation_us: float = 0.0

    def as_dict(self) -> dict:
        return {
            "runtime_us": self.runtime_us,
            "runtime_ms": self.runtime_us / 1000.0,
            "us_by_type": dict(self.us_by_type),
            "us_by_class": dict(self.us_by_class),
            "cooling_us": self.cooling_us,
            "quanta_per_data_ion": dict(self.quanta_per_data_ion),
            "quanta_per_data_ion_total": self.quanta_per_data_ion_total,
            "quanta_per_data_ion_min": self.quanta_per_data_ion_min,
            "quanta_per_data_ion_max": self.quanta_per_data_ion_max,
            "junction_transits_per_data_ion": self.junction_transits_per_data_ion,
            "junction_transits_min": self.junction_transits_min,
            "junction_transits_max": self.junction_transits_max,
            "peak_quanta": self.peak_quanta,
            "peak_quanta_ion": self.peak_quanta_ion,
            "n_gate_pairs": self.n_gate_pairs,
            "gate_error_sum": self.gate_error_sum,
            "mean_gate_quanta": self.mean_gate_quanta,
            "max_gate_quanta_seen": self.max_gate_quanta_seen,
            "idle_error": self.idle_error,
            "spam_error": self.spam_error,
            "neg_log_fidelity": self.neg_log_fidelity,
            "counterfactual_rotation_us": self.counterfactual_rotation_us,
        }


def t2_metrics(
    arch: Architecture,
    res: ReplayResult,
    model: CostModel,
    *,
    data_ion_prefix: str = "d",
) -> T2Metrics:
    data_ions = sorted(i for i in res.per_ion_quanta if i.startswith(data_ion_prefix))
    per_ion_totals = [
        sum(res.per_ion_quanta[i].values()) for i in data_ions
    ] or [0.0]
    components = {c: 0.0 for c in QUANTA_COMPONENTS}
    for i in data_ions:
        for c, v in res.per_ion_quanta[i].items():
            components[c] = components.get(c, 0.0) + v
    n = len(data_ions) or 1
    components = {c: v / n for c, v in components.items()}

    transits = [res.junction_transits.get(i, 0) for i in data_ions] or [0]

    # cooling is a named component of runtime, not an unlabelled part of it (R7c)
    cooling_us = res.us_by_type.get("cool", 0.0)

    # the counterfactual PLAN §0.5 states: the same rotation with no degree-3 node on
    # the path, i.e. every hop paying only the shuttle
    counterfactual = 0.0
    if getattr(model, "models_time", False):
        try:
            shuttle = arch.primitives.curve("shuttle_segment").pick(
                getattr(model, "policy", None) or _default_policy()
            )
            # from the hop counter, which is always populated -- `res.cycles` is empty
            # whenever the caller passed keep_cycles=False, and a counterfactual that
            # silently reads 0.0 is worse than one that is absent
            rotate_hops = sum(
                v for k, v in res.hops_by_class.items() if k.startswith("rotate")
            )
            counterfactual = rotate_hops * shuttle.us * getattr(model, "corner_hops", 1)
        except KeyError:
            counterfactual = 0.0

    t_coh_s = float(arch.species.get("T_coh_s", 0.0) or 0.0)
    idle_error = 0.0
    if t_coh_s:
        idle_error = len(res.per_ion_quanta) * (res.total_us * 1e-6) / t_coh_s

    n_pairs = res.n_gate_pairs or 1

    # -ln F ~ sum_gates eps(n-bar) + n T_exe / T_coh + sum_spam eps_spam  (PLAN 6)
    spam_error = 0.0
    try:
        spam_error += res.n_measure * (
            1.0 - float(arch.primitives.scalar("measure").get("fidelity", 1.0))
        )
    except KeyError:
        pass
    try:
        spam_error += res.n_reset * float(arch.primitives.scalar("reset").get("error", 0.0))
    except KeyError:
        pass

    return T2Metrics(
        runtime_us=res.total_us,
        us_by_class=dict(res.us_by_class),
        us_by_type=dict(res.us_by_type),
        cooling_us=cooling_us,
        quanta_per_data_ion=components,
        quanta_per_data_ion_total=sum(components.values()),
        quanta_per_data_ion_min=min(per_ion_totals),
        quanta_per_data_ion_max=max(per_ion_totals),
        junction_transits_per_data_ion=mean(transits),
        junction_transits_min=min(transits),
        junction_transits_max=max(transits),
        peak_quanta=res.peak_quanta,
        peak_quanta_ion=res.peak_quanta_ion,
        n_gate_pairs=res.n_gate_pairs,
        mean_gate_quanta=res.gate_quanta_sum / n_pairs,
        max_gate_quanta_seen=res.max_gate_quanta_seen,
        gate_error_sum=res.gate_error_sum,
        idle_error=idle_error,
        spam_error=spam_error,
        neg_log_fidelity=res.gate_error_sum + idle_error + spam_error,
        counterfactual_rotation_us=counterfactual,
    )


def _default_policy():
    from ..arch import OperatingPointPolicy

    return OperatingPointPolicy("qccdsim_jones", "fastest")
