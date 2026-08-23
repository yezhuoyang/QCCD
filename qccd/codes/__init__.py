"""Layer 0 -- the code layer.  PLAN §7.

Supplies the interaction multiset a compiler has to realize.  It stops there: which
ancilla serves which check, and in what order, is a scheduling decision, not a property
of the code.
"""

from __future__ import annotations

from .bb import BBCode, Check, gross_code

__all__ = ["BBCode", "Check", "gross_code"]
