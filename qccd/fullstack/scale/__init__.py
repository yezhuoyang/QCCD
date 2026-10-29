"""Shor at scale (docs/FULLSTACK_PLAN.md, slice S8): the run is never written down; the program is.

    expr.py      FormalRV's index arithmetic, parsed once, a power under a remainder taken modulo
    program.py   the symbolic program: a FormalRV `.qec` lowered once per structure -- loops stay
                 loops, data decisions are slots, straight runs are templates stored once -- and
                 sums over its run (instructions, machine time) without expanding it
    compile.py   every template compiled ONCE on a district by the town's own scheduler, and
                 checked there by the place and machine rules
"""
