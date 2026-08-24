"""Layer 3 -- verification.  PLAN §5.

`verify` is the single entry point: replay a program under a model, run every checkable
rule, and check the program's own claims against what the replay computed (R9).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from ..arch import Architecture
from ..cost.models import CostModel
from ..ir.tsir import TSIR
from .control import ChannelBank, ControlRecord, ControlTrace, control_trace
from .replay import CycleRecord, ReplayError, ReplayResult, replay
from .rules import (
    CYCLE_RULES,
    architecture_violations,
    concurrency_violations,
    CycleView,
    ResolvedMove,
    RuleReport,
    Violation,
    rule_statements,
)

__all__ = [
    "ChannelBank",
    "ControlRecord",
    "ControlTrace",
    "control_trace",
    "CycleRecord",
    "CycleView",
    "ReplayError",
    "ReplayResult",
    "ResolvedMove",
    "RuleReport",
    "Violation",
    "architecture_violations",
    "concurrency_violations",
    "replay",
    "rule_statements",
    "verify",
    "verify_metrics",
    "VerificationReport",
]


#: Rules that no amount of replaying can settle, and why.  A verifier that reports these
#: as passing would be lying.
UNCHECKABLE: Mapping[str, str] = {
    "R7b": "no per-zone duty-cycle budget is declared by any architecture yet",
    "R10": "needs symbolic permutation + Pauli-frame tracking against a QASM DAG",
}

#: Rules checked outside the per-cycle loop, so `only_rules` may legitimately name them.
PROGRAM_RULES = ("R9", "R7c", "R18")

PARTIAL: Mapping[str, str] = {
    "R15": (
        "quanta are composed additively, which R15 says is an upper bound; the "
        "interference term needs a secular-phase model the corpus does not supply"
    ),
}


@dataclass
class VerificationReport:
    result: ReplayResult
    rules: RuleReport
    metrics: dict = field(default_factory=dict)

    def ok(self) -> bool:
        return self.rules.ok()

    def summary(self) -> dict:
        return {
            "program": self.result.program_name,
            "architecture": self.result.arch_name,
            "model": dict(self.result.model),
            "metrics": self.result.metrics(),
            "r9": self.metrics,
            "rules": self.rules.summary(),
        }


def verify_metrics(
    prog: TSIR, res: ReplayResult, *, rel_tol: float = 0.0, model_name: str | None = None
) -> tuple[dict, list[Violation]]:
    """R9 -- every claim the program carries must equal what the replay computed.

    Checked at three granularities, because a total can agree by cancellation:
    program totals, then per-batch cost/steps/contacts, then instruction annotations.
    """
    violations: list[Violation] = []
    report: dict = {"checked": [], "claims": {}, "replayed": {}}
    # A claim belongs to the model that produced it.  The deck's 397184/8808 are
    # facts about the deck's cost model, not about the program, so checking them
    # against a replay under a different model would be a category error -- and a
    # red R9 that means nothing is worse than a skipped one that says why.
    claims_model = prog.meta.get("metrics_model")
    if claims_model is not None and model_name is not None and claims_model != model_name:
        report["skipped"] = (
            f"the program's claims were computed under the {claims_model!r} cost "
            f"model; this replay used {model_name!r}"
        )
        return report, violations

    def _cmp(label: str, claimed, got, instr_id: int = -1) -> None:
        report["checked"].append(label)
        report["claims"][label] = claimed
        report["replayed"][label] = got
        if claimed is None:
            return
        if rel_tol and claimed:
            ok = abs(got - claimed) <= rel_tol * abs(claimed)
        else:
            ok = math.isclose(float(got), float(claimed), rel_tol=1e-12, abs_tol=1e-9)
        if not ok:
            violations.append(
                Violation("R9", instr_id, f"{label}: claimed {claimed}, replayed {got}")
            )

    claims = prog.metrics or {}
    if "total_cost" in claims:
        _cmp("total_cost", claims["total_cost"], res.total_cost)
    if "total_steps" in claims:
        _cmp("total_steps", claims["total_steps"], res.total_steps)
    if "total_rotate_hops" in claims:
        hops = sum(v for k, v in res.hops_by_class.items() if k.startswith("rotate"))
        _cmp("total_rotate_hops", claims["total_rotate_hops"], hops)
    if "contacts" in claims:
        _cmp("contacts", claims["contacts"], res.n_gate_pairs)
    if "batches" in claims:
        _cmp("batches", claims["batches"], len(res.per_batch))
    if "runtime_us" in claims:
        _cmp("runtime_us", claims["runtime_us"], res.total_us)
    if "total_quanta" in claims:
        _cmp("total_quanta", claims["total_quanta"], res.total_quanta())
    if "cooling_us" in claims:
        _cmp("cooling_us", claims["cooling_us"], res.us_by_type.get("cool", 0.0))
    if "peak_quanta" in claims:
        _cmp("peak_quanta", claims["peak_quanta"], res.peak_quanta)

    n_batch_bad = 0
    for claim in prog.meta.get("batch_claims", ()):
        got = res.per_batch.get(claim["batch"])
        if got is None:
            violations.append(
                Violation("R9", -1, f"batch {claim['batch']} never replayed")
            )
            n_batch_bad += 1
            continue
        for field_name in ("cost", "steps", "contacts"):
            if not math.isclose(
                float(got[field_name]), float(claim[field_name]), rel_tol=1e-12, abs_tol=1e-9
            ):
                violations.append(
                    Violation(
                        "R9",
                        -1,
                        f"batch {claim['batch']} {field_name}: claimed "
                        f"{claim[field_name]}, replayed {got[field_name]}",
                    )
                )
                n_batch_bad += 1
    report["batches_checked"] = len(prog.meta.get("batch_claims", ()))
    report["batches_mismatched"] = n_batch_bad

    n_instr_bad = 0
    n_instr_checked = 0
    by_instr: dict[int, list] = {}
    for c in res.cycles:
        slot = by_instr.setdefault(c.instr_id, [0.0, 0, c.t0, c.t1])
        slot[0] += c.cost
        slot[1] += c.depth
        slot[2] = min(slot[2], c.t0)
        slot[3] = max(slot[3], c.t1)
    annotated = [
        i
        for i in prog.instructions
        if i.cost is not None or i.steps is not None or i.t0 is not None or i.t1 is not None
    ]
    for instr in annotated:
        # instruction-level claims are checked through the batch aggregation above;
        # here only the annotations attached to a single instruction
        if instr.id not in by_instr:
            continue
        n_instr_checked += 1
        cost, steps, t0, t1 = by_instr[instr.id]
        if instr.cost is not None and not math.isclose(cost, float(instr.cost), abs_tol=1e-9):
            violations.append(
                Violation("R9", instr.id, f"cost: claimed {instr.cost}, replayed {cost}")
            )
            n_instr_bad += 1
        if instr.steps is not None and steps != int(instr.steps):
            violations.append(
                Violation("R9", instr.id, f"steps: claimed {instr.steps}, replayed {steps}")
            )
            n_instr_bad += 1
        for label, claimed, got in (("t0", instr.t0, t0), ("t1", instr.t1, t1)):
            if claimed is not None and not math.isclose(
                float(claimed), float(got), rel_tol=1e-12, abs_tol=1e-9
            ):
                violations.append(
                    Violation("R9", instr.id, f"{label}: claimed {claimed}, replayed {got}")
                )
                n_instr_bad += 1
    report["instructions_annotated"] = len(annotated)
    report["instructions_checked"] = n_instr_checked
    report["instructions_mismatched"] = n_instr_bad
    # a claim the replay does not compute is not silently passed over
    report["not_checked"] = [
        k
        for k in ("quanta_delta", "operating_point")
        if any(getattr(i, k) for i in prog.instructions)
    ]
    if len(annotated) != n_instr_checked:
        report["annotation_coverage_incomplete"] = (
            f"{len(annotated) - n_instr_checked} annotated instruction(s) left no trace "
            f"to check against (replay was run with keep_cycles=False)"
        )
    return report, violations


def verify(
    prog: TSIR,
    arch: Architecture,
    model: CostModel,
    *,
    only_rules: Sequence[str] | None = None,
    check_metrics: bool = True,
    keep_cycles: bool = True,
    rule_config: Mapping[str, object] | None = None,
) -> VerificationReport:
    """Replay, rule-check, and check the program's claims.  The one entry point.

    `rule_config` overrides a rule's own parameters -- today, R7's `max_gate_quanta`.
    `replay` has always accepted it and `qccd.compile.cooling` has always passed it, so
    without it here a budget sweep changes the schedule and is then judged against the
    architecture's original budget: every point but the default reads as an R7 failure,
    and the sweep reports nothing about the trade-off it exists to measure.
    """
    res = replay(
        prog, arch, model, check_rules=True, only_rules=only_rules,
        keep_cycles=keep_cycles, rule_config=rule_config
    )
    rules = res.rules
    # only rules that HAVE an implementation and were actually run may be marked
    # checked; naming a rule in `only_rules` is a request, not a certificate
    requested = set(CYCLE_RULES) if only_rules is None else set(only_rules)
    rules.checked |= requested & set(CYCLE_RULES)
    for unknown in sorted(requested - set(CYCLE_RULES) - set(PROGRAM_RULES)):
        rules.skipped[unknown] = "no per-cycle check is implemented for this rule"
    if only_rules is None or "R11" in only_rules:
        rules.extend(architecture_violations(arch))
    if only_rules is None or "R4" in only_rules:
        rules.extend(concurrency_violations(arch, prog))
    metrics: dict = {}
    if check_metrics:
        metrics, viol = verify_metrics(prog, res, model_name=model.name)
        rules.extend(viol)
        if metrics.get("skipped"):
            rules.skipped["R9"] = metrics["skipped"]
        else:
            rules.checked.add("R9")
            gaps = []
            if metrics.get("annotation_coverage_incomplete"):
                gaps.append(metrics["annotation_coverage_incomplete"])
            if metrics.get("not_checked"):
                gaps.append(
                    "the replay computes no per-instruction "
                    + "/".join(metrics["not_checked"])
                    + ", so those annotations are unchecked"
                )
            if gaps:
                rules.partial["R9"] = "; ".join(gaps)
    else:
        rules.skipped["R9"] = "check_metrics=False: the program's claims were not checked"
    rules.checked.add("R18")  # charged by degree, by construction of the cost model
    for rule, why in UNCHECKABLE.items():
        rules.skipped[rule] = why
    for rule, why in PARTIAL.items():
        rules.partial[rule] = why
        rules.checked.add(rule)
    if model.models_heating:
        rules.checked.update({"R16", "R17"})
    else:
        rules.skipped["R16"] = "this cost model does not model heating"
        rules.skipped["R17"] = "this cost model does not model elapsed time"
    if not model.models_heating:
        rules.skipped["R7c"] = "cooling legality is only meaningful once heating is modelled"
    elif only_rules is None or "R7c" in only_rules:
        rules.extend(_check_r7c(prog, res))
        rules.checked.add("R7c")
    return VerificationReport(result=res, rules=rules, metrics=metrics)


def _check_r7c(prog: TSIR, res: ReplayResult) -> list[Violation]:
    """R7c -- cooling is mandatory, not optional.

    A program that runs two-qubit gates under a heating model and schedules no cooling at
    all is illegal by R7c regardless of whether any individual gate happens to squeak
    under R7's budget: without cooling a broadcast-wired machine cannot pass a logical
    error rate of 1e-4 (2510.23519, 2606.06455).
    """
    if not res.n_gate_pairs:
        return []
    n_cool = sum(1 for i in prog.instructions if i.type == "cool")
    if n_cool:
        return []
    return [
        Violation(
            "R7c",
            -1,
            f"{res.n_gate_pairs} two-qubit gates and no cooling operation anywhere; "
            f"peak n-bar reaches {res.peak_quanta:.1f} quanta",
        )
    ]
