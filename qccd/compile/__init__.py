"""Layer 5 -- the compilation pipeline.  PLAN §7.

Implemented so far: pass 6, cooling insertion.  Placement, interaction order, routing,
SIMD aggregation, operating-point selection and event scheduling are later milestones.
"""

from __future__ import annotations

from .cooling import ION_LOSS_ROUND_TRIPS, CoolingPolicy, CoolingResult, insert_cooling
from .oddeven import odd_even_sort_program
from .pipeline import CompilePolicy, CompileResult, compile_code
from .programs import BUILDERS, build, from_deck, odd_even, rotate, walk

__all__ = [
    "ION_LOSS_ROUND_TRIPS",
    "CoolingPolicy",
    "CoolingResult",
    "insert_cooling",
    "odd_even_sort_program",
    "CompilePolicy",
    "CompileResult",
    "compile_code",
    "BUILDERS",
    "build",
    "from_deck",
    "odd_even",
    "rotate",
    "walk",
]
