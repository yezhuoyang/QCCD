"""The leaderboard metrics, computed from the toolchain's own functions.

These are the numbers the boards publish, defined where the boards compute them
(`Codesign/scripts/small_codes.py::compile_point` and `q06_campaign.py::broadcast_use`),
restated here as a library so the evaluator does not import maintainer scripts.  Every
value is a function of (architecture, final program, task physics) only -- never of the
evaluator's wall-clock time, so a faster machine cannot improve a score.

`tests/test_workspace_metrics.py` holds these equal to the published rows.
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
                "total_cost": "model cost units", "total_steps": "machine steps"}


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
    for ins in prog.instructions:
        if ins.type != "simd":
            continue
        if ins.cls and ins.cls.startswith("rotate"):
            delta = abs(int((ins.template or {}).get("delta", 1)))
            ion_moves += riders * delta
            ions_per.append(riders)
        elif ins.participants:
            ion_moves += len(ins.participants)
            ions_per.append(len(ins.participants))
    return {"transport_instructions": len(ions_per), "ion_moves": ion_moves,
            "ions_per_instruction": (sum(ions_per) / len(ions_per)) if ions_per else 0.0}


def compute_metrics(arch: Architecture, prog: TSIR, physics: Mapping) -> tuple:
    """`(metrics, breakdown)` for one design under the task's physics tables."""
    from ..cost.hardware import hardware_report

    rank_table = physics.get("rank_table", "qccdsim_jones")
    tables = list(physics.get("tables") or [rank_table])
    out: dict = {}
    breakdown: dict = {}
    for table in tables:
        ms, res = replay_time(arch, prog, table)
        tag = {"qccdsim_jones": "T_jones", "transport_excitation": "T_transport"}.get(table, f"T_{table}")
        out[tag] = ms
        breakdown[table] = {"total_us": res.total_us, "total_cost": res.total_cost,
                            "total_steps": res.total_steps,
                            "us_by_type": dict(getattr(res, "us_by_type", {}) or {}),
                            "us_by_class": dict(getattr(res, "us_by_class", {}) or {}),
                            "peak_quanta": getattr(res, "peak_quanta", None)}
    bc = broadcast_use(arch, prog)
    out["ions_per_instruction"] = bc["ions_per_instruction"]
    out["transport_instructions"] = bc["transport_instructions"]
    out["instructions"] = len(prog)
    out["dacs"] = hardware_report(arch).dacs
    breakdown["broadcast"] = bc
    return out, breakdown
