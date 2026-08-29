"""The figure table is the audit, pinned.

`tools/rule_figs_spec.py` declares, for every term and every rule, a legal programme and
an illegal one **and the exact rule set each must fire**. Rendering them is slow; checking
them is not, and the check is the part that matters -- it is the isolation argument of the
rules review (`docs/notes.md` section 4.1) in executable form.

Two properties are asserted here and nowhere else:

* every declared verdict still holds -- a figure cannot ship a picture the verifier
  disagrees with;
* every rule that has a per-cycle implementation is ISOLATED by at least one figure, i.e.
  some programme trips it and nothing else. That is what "no rule is redundant" means
  operationally, and a rule that loses its isolating witness is a rule that has become
  derivable from another one.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

pytest.importorskip("PIL", reason="the figure tool renders with Pillow")

from qccd.verify.rules import CYCLE_RULES  # noqa: E402
from rule_figs_spec import SPECS  # noqa: E402

FIGS = SPECS()
CASES = [(f.key, i, c) for f in FIGS for i, c in enumerate(f.cases)]


def test_the_table_is_not_empty():
    assert len(FIGS) >= 25, "the figure set should cover every term and every rule"
    assert CASES


@pytest.mark.parametrize("key,i,case", CASES, ids=[f"{k}[{i}]" for k, i, _ in CASES])
def test_each_case_fires_exactly_what_it_claims(key, i, case):
    """`Case.run` raises unless the observed rule set equals the declared one."""
    case.run()


def test_every_implemented_rule_has_an_isolating_figure():
    """One figure trips it and nothing else -- the operational form of 'not redundant'.

    R5 is the documented exception in the other direction: it is isolated only on a
    segment of capacity >= 2, which no shipped architecture declares, so its figure builds
    that stage explicitly. If this test starts failing for some other rule, that rule has
    become derivable from another and the rule list needs revisiting, not the test.
    """
    isolated = {tuple(c.expect)[0] for _, _, c in CASES if len(c.expect) == 1}
    # `r4_drivable` is registered under the key "R4d" but emits `Violation("R4", ...)`
    # (docs/notes.md 5.1), so no violation anywhere in the tree is labelled R4d and it cannot
    # be isolated BY LABEL. Its unique clause is isolated by `R4d2_switch_per_site`, which
    # fires R4 alone on a device with no per-site switches -- see the next test, which
    # fails when the labelling is fixed and this exemption should be removed.
    label_defect = {"R4d"}
    missing = sorted(set(CYCLE_RULES) - isolated - label_defect)
    assert not missing, (
        f"no figure isolates {missing}; every per-cycle rule needs a programme that "
        f"trips it ALONE, or it is not carrying its own weight")


def test_r4d_still_emits_under_r4s_name():
    """The defect named in `docs/notes.md` 5.1, pinned so its fix is noticed.

    `CYCLE_RULES["R4d"] = r4_drivable`, but every violation it constructs is labelled
    "R4". `RuleReport.failed()` reads the label while `checked` holds the key, so R4d
    lands in `passed()` on the very cycle its own check rejected. When this test starts
    failing, the fix has landed: drop `label_defect` from the test above, and change
    `R4d2_switch_per_site`'s expectation from ("R4",) to ("R4d",).
    """
    from qccd.cost import deck_model
    from qccd.verify import verify
    from make_rule_figs import init, move, prog, wired_ring

    arch = wired_ring(switch_per_site=False)
    rep = verify(prog(init({"a": "S1"}), move(("a", "S1", "S2"))), arch,
                 deck_model(), check_metrics=False)
    fired = {v.rule for v in rep.rules.violations}
    assert fired == {"R4"}, f"expected the drivability check to emit as R4, got {fired}"
    assert "R4d" in rep.rules.summary()["passed"], (
        "R4d used to be reported as passing on a cycle it rejected")


def test_r5_is_isolated_only_by_widening_a_segment():
    """The audit's sharpest finding, pinned: at segment.capacity 1 an exchange is two
    ions on one segment, so R3 fires too and R5 adds nothing. Every shipped device is
    capacity 1."""
    from qccd.arch import load
    from qccd.cost import deck_model
    from qccd.verify import verify
    from make_rule_figs import init, move, prog, two_traps

    swap = (("x", "T0", "T1"), ("y", "T1", "T0"))
    for cap, expect in ((1, {"R3", "R5"}), (2, {"R5"})):
        arch = two_traps(cap)
        rep = verify(prog(init({"x": "T0", "y": "T1"}), move(*swap)), arch,
                     deck_model(), check_metrics=False)
        assert {v.rule for v in rep.rules.violations} == expect, f"capacity {cap}"

    for name in ("ring144_24v", "grid9x9", "chain", "h2_racetrack", "ladder_2x72",
                 "cyclone_base", "cyclone_dual_loop", "deck_unit_cell",
                 "stationary_chain"):
        dev = load(ROOT / "arch" / f"{name}.arch.json").device
        caps = {s.capacity for s in dev.segments.values()}
        assert caps == {1}, f"{name} declares segment capacities {caps}"
