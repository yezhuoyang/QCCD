"""Debugging a programme step by step: the model behind the website's Debug page and the
agent's `qccd_debug` tool.

    from qccd.debug import open_files
    s = open_files("chain4.arch.json", "prog.tsir.json")
    s.state(4, watch=["q0", "C0"])                 # where everything is after step 4
    s.check("standard", 'for all s in sites, t in instants: occupancy(s, t) <= 1')
    s.find('occupancy("C0", t) > 1')              # a conditional breakpoint

A step is one instruction by its position, which is also the frame of the Studio's
animation, so whatever this reports at step k the page shows at frame k.  The rules and the
expressions are the rule language of `qccd.ruleset` (the website's Rules page), judged by
the grader's own evaluator; see docs/debug.md.
"""

from .session import (DebugError, Failure, Session, assertion_rules, model_from, open_documents,
                      open_files)

__all__ = ["Session", "Failure", "DebugError", "open_files", "open_documents", "assertion_rules",
           "model_from"]
