"""Every example on the Rules page gets the verdict its card says it gets.

The page prints each rule beside a programme that keeps it and one that breaks it, judged by the
standard rule set.  Two cards had drifted: R9's "true totals" claimed the totals from before a
move into an occupied trap was charged its merge, so the passing example failed; and R8's
double-listed ion read as one walk, so the failing example held.  Both are caught here.
"""

from __future__ import annotations

import pytest

pytest.importorskip("qccd.site.examples", reason="the site package is not in this tree")

from qccd.cost import corrected_model, deck_model  # noqa: E402
from qccd.site.examples import RULES, judge_by_rules, machine  # noqa: E402

CASES = [(R["id"], which) for R in RULES for which in ("pass", "fail") if which in R]
EXPECT = {"skipped": "does not apply"}


@pytest.mark.parametrize("rid,which", CASES, ids=[f"{r}-{w}" for r, w in CASES])
def test_the_card_gets_the_verdict_it_says(rid, which):
    R = next(x for x in RULES if x["id"] == rid)
    ex = R[which]
    if ex.get("expect") == "any":
        pytest.skip("this card shows the verdict without claiming one")
    m = machine(ex.get("device") or R["device"])
    prog = ex["ir"](m) if ex.get("ir") else m.program("example", provenance="off").apply_calls(ex["program"]).build()
    model = deck_model() if (ex.get("model") or R.get("model", "corrected")) == "deck" else corrected_model()
    got = judge_by_rules(m, prog, model, rid, R.get("check_metrics", False), R.get("ruleset", "standard")).get("focus")
    if got is None:
        pytest.skip(f"{rid} is not in the {R.get('ruleset', 'standard')} rule set's verdicts")
    want = EXPECT.get(ex.get("expect"), "holds" if which == "pass" else "fails")
    if want == "holds":
        assert got["verdict"] in ("holds", "does not apply"), (rid, which, got)
    else:
        assert got["verdict"] == want, (rid, which, got)


def test_the_junction_rule_is_shown_on_a_junction():
    """R2 is about junctions, so both its examples carry ions through a node the device draws
    as a junction -- not through a trap site where three rails happen to meet, which is what
    the cards once used (reported on the page, 2026-10-20)."""
    R = next(x for x in RULES if x["id"] == "R2")
    m = machine(R["device"])
    dev = m.arch.device
    for which in ("pass", "fail"):
        prog = m.program("example", provenance="off").apply_calls(R[which]["program"]).build()
        crossed = {n for ins in prog.instructions for p in ins.participants
                   for seg in p.via for n in dev.segments[seg].ends}
        assert any(dev.nodes[n].kind == "junction" for n in crossed), (which, sorted(crossed))
