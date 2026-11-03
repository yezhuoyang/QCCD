"""Shor at scale (docs/FULLSTACK_PLAN.md, slice S8): the run is never written down; the program is.

    expr.py      FormalRV's index arithmetic, parsed once, a power under a remainder taken modulo
    program.py   the symbolic program: a FormalRV `.qec` lowered once per structure -- loops stay
                 loops, data decisions are slots, straight runs are templates stored once -- and
                 sums over its run (instructions, machine time) without expanding it
    compile.py   every template compiled ONCE on a district by the town's own scheduler, and
                 checked there by the place and machine rules
    machine.py   the machine it runs on at RSA-2048 (slice S9): distance 11, its physics and noise
    lattice.py   the patch-lattice scheduler on that machine: products as corridor merges between
                 stationary tiles, every gadget a copy of a program checked once
    relay.py     a long product as a chain of neighbour merges, proved the same; a CNOT's ancilla
                 at the crossing of its control's column and its target's row
    czpair.py    a CZ by a pair made ahead, proved the same: its H̄ before the guard, not after
"""
