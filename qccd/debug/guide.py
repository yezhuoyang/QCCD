"""The review guide: what a rule's failure usually means and how to fix it, rule by rule.

`guide.md` beside this file is written by hand and read the same way by a person and by their
agent: the Debug page shows a rule's row beside its failure, and an agent reviewing a design reads
the whole file (the workspace skill carries it).  This module reads its tables; a test keeps one
row for every rule of every shipped set.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

__all__ = ["GUIDE", "guide", "guide_text"]

GUIDE = Path(__file__).with_name("guide.md")
_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$")


def guide_text() -> str:
    return GUIDE.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def guide() -> dict[str, dict[str, str]]:
    """{rule id: {"means": ..., "fix": ...}} from the guide's "Usually means | Fix" tables (the
    machine rules, the machine rules, and the machine stage's budget as "budget")."""
    out: dict[str, dict[str, str]] = {}
    table = False
    for line in guide_text().splitlines():
        m = _ROW.match(line)
        if not m:
            table = False
            continue
        a, b, c = m.groups()
        if a == "Rule" and b == "Usually means":
            table = True
            continue
        if not table or set(a) <= {"-"}:
            continue
        rid = "budget" if a.startswith("budget") else a
        out[rid] = {"means": b, "fix": c}
    return out
