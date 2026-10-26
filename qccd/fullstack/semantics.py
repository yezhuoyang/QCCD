"""What a lowered program computes: the gadget library's symbolic stabilizer semantics.

The simulator lives with the rest of the gadget logic (`qccd.gadget.logic.symbolic`): it is how
the compositional sign-off decides a program too big to enumerate.  The full stack uses it to
check a FormalRV program's outcome spec exactly, in every branch at once.
"""
from ..gadget.logic.symbolic import NotClifford, check_outcome_spec, expr_text, run  # noqa: F401
