"""The leaderboard metrics: the evaluation text's, read by its interpreter.

Every number `compute_metrics` publishes is a `metric` of `qccd/evalset/library/metrics.eval`
-- the definitions the Evaluation page prints -- so the grader, the workspace and the boards
compute exactly what the text says.  Every value is a function of (architecture, final
program, task physics) only, never of the evaluator's wall-clock time, so a faster machine
cannot improve a score.  `replay_time` and `broadcast_use` remain for the callers that want
the replay itself; `tests/test_workspace_metrics.py` holds the metrics equal to the
published rows and `tests/test_evalset.py` to these functions.
"""

from __future__ import annotations

from typing import Mapping

from ..arch.device import Architecture
from ..cost.models import corrected_model
from ..ir.tsir import TSIR
from ..verify.replay import replay

__all__ = ["replay_time", "broadcast_use", "compute_metrics", "METRIC_UNITS"]

METRIC_UNITS = {"T_jones": "ms", "T_transport": "ms", "instructions": "count", "dacs": "count",
                "ions_per_instruction": "ions", "transport_instructions": "count",
                "total_cost": "model cost units", "total_steps": "machine steps",
                "ler": "per experiment", "ler_per_round": "per round",
                "ler_per_round_per_logical": "per round per logical qubit"}


def _fastest_model(table: str):
    """The cost model at each primitive's fastest operating point.  A cost model with
    operating-point policies takes `objective`; one without them has a single point per
    primitive, which is then the fastest."""
    import inspect
    if "objective" in inspect.signature(corrected_model).parameters:
        return corrected_model(table, objective="fastest")
    return corrected_model(table)


def replay_time(arch: Architecture, prog: TSIR, table: str) -> tuple:
    """Modelled execution time of `prog` on `arch` under one physics table (objective
    `fastest`, as the boards use): `(ms, ReplayResult)`."""
    model = _fastest_model(table)
    res = replay(prog, arch, model, check_rules=False, keep_cycles=False)
    return res.total_us / 1000.0, res


def broadcast_use(arch: Architecture, prog: TSIR) -> dict:
    """Ions moved per transport instruction (q06_campaign.broadcast_use, same formula):
    a rotation counts every ion riding a closed loop; any other simd counts its
    participants."""
    loop_nodes: set = set()
    for lp in arch.device.loops.values():
        if lp.closed:
            loop_nodes.update(lp.nodes)
    init = next((i for i in prog.instructions if i.type == "init"), None)
    riders = sum(1 for s in (init.placement.values() if init else ()) if s in loop_nodes)
    ions_per: list = []
    ion_moves = 0
    # a C2LR step names no participants: its movers are the device's matching (docs/c2lr.md)
    c2lr_movers: dict[int, int] = {}
    if any((i.template or {}).get("kind") in ("swap_or_stay", "reorder_or_not")
           for i in prog.instructions):
        from ..verify.c2lr import walk
        c2lr_movers = {ins.id: len(step.movers) for ins, step, _ in walk(prog, arch)
                       if step is not None and step.template}
    for ins in prog.instructions:
        if ins.type != "simd":
            continue
        if ins.id in c2lr_movers:
            ion_moves += c2lr_movers[ins.id]
            ions_per.append(c2lr_movers[ins.id])
        elif ins.cls and ins.cls.startswith("rotate"):
            delta = abs(int((ins.template or {}).get("delta", 1)))
            ion_moves += riders * delta
            ions_per.append(riders)
        elif ins.participants:
            ion_moves += len(ins.participants)
            ions_per.append(len(ins.participants))
    return {"transport_instructions": len(ions_per), "ion_moves": ion_moves,
            "ions_per_instruction": (sum(ions_per) / len(ions_per)) if ions_per else 0.0}


#: The board metric a table's execution time is published under.
TIME_METRIC = {"qccdsim_jones": "T_jones", "transport_excitation": "T_transport", "quantinuum": "T_quantinuum"}


def compute_metrics(arch: Architecture, prog: TSIR, physics: Mapping) -> tuple:
    """`(metrics, breakdown)` for one design under the task's physics tables.

    Every value is a metric of the evaluation text `qccd/evalset/library/metrics.eval`, read
    by its interpreter: the execution time under each table (`T_jones`, `T_transport`,
    `T_quantinuum`, each with its own clock; any other table's as `T` on that clock),
    `instructions`, `transport_instructions`, `ions_per_instruction` and `dacs` (counted with
    the physics' floors, as the device stage judges the budget)."""
    from ..evalset import load

    rank_table = physics.get("rank_table", "qccdsim_jones")
    tables = list(physics.get("tables") or [rank_table])
    ev = load("metrics").with_clock(rank_table)
    e = ev.evaluator(ev.run(prog, arch))
    names = {m.name for m in ev.metrics_}
    out: dict = {}
    breakdown: dict = {}
    for table in tables:
        tag = TIME_METRIC.get(table, f"T_{table}")
        sub = e.under(table)
        out[tag] = e.value(tag) if tag in names else sub.value("T")
        res = sub.run.result
        breakdown[table] = {"total_us": res.total_us, "total_cost": res.total_cost,
                            "total_steps": res.total_steps,
                            "us_by_type": dict(getattr(res, "us_by_type", {}) or {}),
                            "us_by_class": dict(getattr(res, "us_by_class", {}) or {}),
                            "peak_quanta": getattr(res, "peak_quanta", None)}
    for k in ("ions_per_instruction", "transport_instructions", "instructions", "dacs"):
        out[k] = e.value(k)
    breakdown["broadcast"] = broadcast_use(arch, prog)
    breakdown["evaluation"] = {"name": ev.name, "digest": ev.digest()}
    return out, breakdown
