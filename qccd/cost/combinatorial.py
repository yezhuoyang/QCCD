"""T1 -- the combinatorial tier of the objective.  PLAN §6.

Cheap enough to sit inside an optimization loop.  Every quantity here is a count, and
the two that matter most for the shipped design are the ones PLAN §0.1 calls out:
`cost` is 99.6% rigid rotation, and contact-batch utilization is 9.1% of the design's
own limit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from ..arch import Architecture
from ..ir.tsir import TSIR
from ..verify.replay import ReplayResult

__all__ = ["T1Metrics", "t1_metrics"]


@dataclass
class T1Metrics:
    total_cost: float
    total_steps: int
    rotate_hops: int
    n_batches: int
    n_contacts: int
    contact_batch_utilization: float
    contact_batch_limit: int
    batch_size_histogram: dict[int, int] = field(default_factory=dict)
    cost_by_class: dict[str, float] = field(default_factory=dict)
    steps_by_class: dict[str, int] = field(default_factory=dict)
    cost_share: dict[str, float] = field(default_factory=dict)
    junction_transits_total: int = 0
    junction_transits_per_data_ion: float = 0.0
    junction_contention: dict[str, int] = field(default_factory=dict)
    n_cooling_ops: int = 0
    n_movement_templates: int = 0
    movement_templates: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "total_cost": self.total_cost,
            "total_steps": self.total_steps,
            "rotate_hops": self.rotate_hops,
            "n_batches": self.n_batches,
            "n_contacts": self.n_contacts,
            "contact_batch_utilization": self.contact_batch_utilization,
            "contact_batch_limit": self.contact_batch_limit,
            "contact_batch_utilization_pct": (
                100.0 * self.contact_batch_utilization / self.contact_batch_limit
                if self.contact_batch_limit
                else float("nan")
            ),
            "batch_size_histogram": dict(sorted(self.batch_size_histogram.items())),
            "cost_by_class": dict(self.cost_by_class),
            "steps_by_class": dict(self.steps_by_class),
            "cost_share": dict(self.cost_share),
            "junction_transits_total": self.junction_transits_total,
            "junction_transits_per_data_ion": self.junction_transits_per_data_ion,
            "n_cooling_ops": self.n_cooling_ops,
            "n_movement_templates": self.n_movement_templates,
            "movement_templates": dict(self.movement_templates),
        }


def t1_metrics(
    prog: TSIR,
    arch: Architecture,
    res: ReplayResult,
    *,
    data_ion_prefix: str = "d",
) -> T1Metrics:
    limit = int(prog.meta.get("active_contact_limit", 0)) or 0
    per_batch = res.per_batch
    n_batches = len(per_batch)
    n_contacts = res.n_gate_pairs
    hist: dict[int, int] = {}
    for d in per_batch.values():
        size = int(d["contacts"])
        hist[size] = hist.get(size, 0) + 1
    util = n_contacts / n_batches if n_batches else 0.0

    rotate_hops = sum(v for k, v in res.hops_by_class.items() if k.startswith("rotate"))
    total = res.total_cost or 1.0
    share = {k: v / total for k, v in res.cost_by_class.items()}

    data_ions = [i for i in res.per_ion_quanta if i.startswith(data_ion_prefix)]
    jt_total = sum(res.junction_transits.values())
    jt_per_data = (
        sum(res.junction_transits[i] for i in data_ions) / len(data_ions)
        if data_ions
        else 0.0
    )
    templates = prog.templates()

    return T1Metrics(
        total_cost=res.total_cost,
        total_steps=res.total_steps,
        rotate_hops=rotate_hops,
        n_batches=n_batches,
        n_contacts=n_contacts,
        contact_batch_utilization=util,
        contact_batch_limit=limit,
        batch_size_histogram=hist,
        cost_by_class=dict(res.cost_by_class),
        steps_by_class=dict(res.steps_by_class),
        cost_share=share,
        junction_transits_total=jt_total,
        junction_transits_per_data_ion=jt_per_data,
        junction_contention=dict(res.junction_transits_by_node),
        n_cooling_ops=sum(1 for c in res.cycles if c.type == "cool"),
        n_movement_templates=len(templates),
        movement_templates=templates,
    )
