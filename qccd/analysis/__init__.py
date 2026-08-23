"""Analyses that answer from the architecture, not from a benchmark someone compiled.

`cost/` prices a programme; `verify/` judges one. Everything in here answers without a
programme at all -- the question an architect is asking while dragging a site.

Two layers: the functions (`reach_report`, `error_budget`) answer one question at one
setting, and the `QCCDAnalysis` classes wrap them in a uniform contract so a tool can
list them, show their knobs, and sweep one -- which is how an architect actually asks.
"""

from .analyses import ANALYSES, BudgetAnalysis, ReachAnalysis, get_analysis
from .base import QCCDAnalysis, SweepPoint, SweepResult
from .budget import BudgetReport, Channel, error_budget
from .reach import ReachReport, distance_matrix, nearest, reach_report

__all__ = ["ReachReport", "reach_report", "distance_matrix", "nearest",
           "BudgetReport", "Channel", "error_budget",
           "QCCDAnalysis", "SweepResult", "SweepPoint",
           "ReachAnalysis", "BudgetAnalysis", "ANALYSES", "get_analysis"]
