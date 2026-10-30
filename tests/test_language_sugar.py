"""The Language page's shorthands mean exactly the core statements it says they stand for.

`fill`, `move`, `shuttle` and `rotate` are shorthands: the page defines each as core
statements (`init`, `simd`) and shows every shorthand example written out that way.  Here
each is judged both ways -- the page's own examples and a few harder cases (a packed loop,
a turn backwards, a turn of two slots, a loop through a junction) -- and the two must agree
in every figure and every verdict, under the legacy verifier and the standard rule set.
"""

from __future__ import annotations

import pytest

pytest.importorskip("qccd.site.examples", reason="the site package is not in this tree")

from qccd.cost import corrected_model  # noqa: E402
from qccd.verify import verify  # noqa: E402
from qccd.site.examples import (DUAL, RACE, VERBS, core_form, judge, judge_by_rules,  # noqa: E402
                                machine, rec)

SHORTHANDS = [v for v in VERBS if v.get("kind") == "shorthand"]
EXTRA = [
    ("rotate a packed loop", RACE, [rec("fill"), rec("rotate", 1)]),
    ("rotate backwards", RACE, [rec("init", {"d0": "S0", "d1": "S2", "d2": "S5"}), rec("rotate", -1)]),
    ("rotate through a junction", DUAL, [rec("init", {"a0": "AT0", "a1": "AT2", "a2": "AB1"}), rec("rotate", 1, loop="A")]),
    ("rotate the packed outer loop", DUAL, [rec("fill", loop="A", prefix="a"), rec("rotate", 3, loop="A")]),
]
CASES = [(f"p.{v['verb']}", v["device"], v["example"]) for v in SHORTHANDS] + EXTRA


def test_the_page_names_four_shorthands_and_nine_core_statements():
    kinds = {}
    for v in VERBS:
        kinds.setdefault(v.get("kind", "core"), []).append(v["verb"])
    assert sorted(kinds["shorthand"]) == ["fill", "move", "rotate", "shuttle"]
    # the two C2LR steps (docs/c2lr.md, 2026-10-30) are core statements: a template the
    # replay expands from the device, which no other statement can write
    assert sorted(kinds["core"]) == ["barrier", "cool", "gate", "init", "measure", "reorder_or_not", "reset",
                                     "simd", "swap_or_stay"]
    assert all(v.get("means") for v in SHORTHANDS)


@pytest.mark.parametrize("name,dev,calls", CASES, ids=[c[0] for c in CASES])
def test_a_shorthand_is_judged_as_its_core_form(name, dev, calls):
    m = machine(dev)
    core = core_form(m, calls)
    assert {c["method"] for c in core} <= {"init", "simd"}, core
    a = m.program("a", provenance="off").apply_calls(calls).build()
    b = m.program("b", provenance="off").apply_calls(core).build()
    ja, jb = judge(m, a, "corrected"), judge(m, b, "corrected")
    for k in ("failed", "cost", "steps", "us", "peak_quanta"):
        assert ja[k] == pytest.approx(jb[k]) if isinstance(ja[k], float) else ja[k] == jb[k], (k, ja[k], jb[k])
    ra = judge_by_rules(m, a, corrected_model(), None, False)
    rb = judge_by_rules(m, b, corrected_model(), None, False)
    assert ra["fails"] == rb["fails"]
    # the figures cannot tell a turn one way from a turn the other way: where every ion
    # ends, and how hot, can
    fa = verify(a, m.arch, corrected_model()).result
    fb = verify(b, m.arch, corrected_model()).result
    assert dict(fa.final_positions) == dict(fb.final_positions)
    assert dict(fa.final_quanta) == pytest.approx(dict(fb.final_quanta))
