"""QCCD compiler and architecture-exploration platform.

Layer map (docs/PLAN.md):

    qccd.arch     Layer 1 -- the architecture description language (`.arch.json`)
    qccd.ir       Layer 2 -- the control IR (TSIR)
    qccd.verify   Layer 3 -- the rules R1..R18, as machine-checkable invariants
    qccd.cost     Layer 4 -- the objective (T1 combinatorial, T2 physical)
    qccd.compile  Layer 5 -- the compilation pipeline (cooling insertion so far)

Everything here is a pure-Python reference implementation.  Per PLAN §8 these are
permanent, not scaffolding: native kernels, when they arrive, are differential-tested
against them.
"""

from .api import Cycle, Machine, Program, RunResult

__version__ = "0.1.0"

__all__ = [
    "Machine",
    "Program",
    "Cycle",
    "RunResult",
    "arch",
    "ir",
    "verify",
    "cost",
    "compile",
    "codes",
    "viz",
]
