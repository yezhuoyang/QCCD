"""One shape for every analysis -- and the four ways that shape can lie.

`QCCDAnalysis` exists so a design tool can offer an analysis it has never heard of: read
`default_setup` for the knobs, `data_labels` for the outputs, call `run`, plot the result.
The contract is only worth having if it fails loudly where it would otherwise be
confidently wrong, so that is most of what is tested here.

**A typo must not be a warning.**  qiskit-metal's `QAnalysis` warns on an unknown setup
key and carries on with the default.  For this project that is the worst available
outcome: the run succeeds, the number looks fine, and it describes a machine nobody asked
about.  `budgt=0.5` raises.

**A declared output must be produced.**  A `data_labels` entry `_run` forgets becomes a
blank axis three steps downstream, where nothing points back at the cause.

**A sweep must survive a bad point.**  Sweeping walks into invalid designs by
construction; losing twenty good points to the twenty-first is how an instrument stops
being used.  And it must leave the analysis as it found it, or every number after a sweep
is quietly about the last setting the sweep happened to try.

**The scales have to compose.**  `BudgetAnalysis.scale` nests `_Scaled` once per channel.
Because the summed error is linear in each channel's contribution (R16), scaling two
channels must move the total by exactly the sum of the two separate moves -- a real
physical check on the plumbing, which a mis-nested wrapper would fail.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.analysis import (BudgetAnalysis, QCCDAnalysis,  # noqa: E402
                           ReachAnalysis, get_analysis, reach_report)
from qccd.arch import load  # noqa: E402
from qccd.cost import corrected_model  # noqa: E402

ARCH = ROOT / "arch"
DEV = str(ARCH / "ring144_24v.arch.json")


# -- the contract -------------------------------------------------------------------

def test_an_unknown_setup_key_is_refused_not_ignored():
    """The flagship. Ignoring it means reporting a number for the default design while
    the architect believes they changed something."""
    with pytest.raises(KeyError) as e:
        ReachAnalysis(device=DEV, budgt=0.5)
    assert "budgt" in str(e.value)
    assert "budget" in str(e.value), "the error should name the keys that do exist"


def test_a_nested_unknown_key_is_refused_too():
    """`scale` is a dict of channels, so a typo hides one level down."""
    with pytest.raises(KeyError) as e:
        BudgetAnalysis(device=DEV, scale={"juntcion": 0.5})
    assert "scale.juntcion" in str(e.value), str(e.value)


def test_setup_update_validates_as_well_as_the_constructor():
    a = ReachAnalysis(device=DEV)
    with pytest.raises(KeyError):
        a.setup_update(metrik="us")


def test_a_declared_output_must_actually_be_produced():
    """A missing label is a blank axis somewhere downstream with nothing pointing back."""

    class Forgetful(QCCDAnalysis):
        default_setup = {"x": 1}
        data_labels = ("a", "b")

        def _run(self):
            return {"a": 1.0}                     # 'b' never arrives

    with pytest.raises(KeyError, match="b"):
        Forgetful().run()


def test_get_data_before_a_run_raises_rather_than_returning_empty():
    a = ReachAnalysis(device=DEV)
    with pytest.raises(RuntimeError):
        a.get_data("n_stranded")


def test_get_data_rejects_a_label_the_analysis_does_not_declare():
    a = ReachAnalysis(device=DEV)
    a.run()
    with pytest.raises(KeyError):
        a.get_data("n_strandd")


def test_run_args_records_what_produced_the_number():
    """Every number in this project traces to what produced it; a report that cannot say
    its own setup breaks that at the last step."""
    a = ReachAnalysis(device=DEV)
    a.run(budget=0.3)
    assert a.run_args["budget"] == 0.3
    assert a.run_args["model"] == "corrected"
    a.run_args["budget"] = 999           # a copy, not the live setup
    assert a.run_args["budget"] == 0.3


def test_describe_is_enough_to_build_a_picker_without_knowing_the_analysis():
    for name in ("reach", "budget"):
        d = get_analysis(name).describe()
        assert d["summary"] and d["data_labels"] and isinstance(d["setup"], dict)
        assert d["name"]


# -- sweeps -------------------------------------------------------------------------

def test_a_sweep_survives_a_failing_point_and_says_which():
    """Losing the whole curve to one invalid design is how an instrument stops being
    used -- but a silently dropped point would draw a gap as if it were continuous."""
    a = ReachAnalysis(device=DEV)
    r = a.sweep("budget", [0.5, "not-a-number", 1.5])
    assert len(r.points) == 3
    assert [p.ok for p in r.points] == [True, False, True]
    assert len(r.failures) == 1
    assert r.failures[0].error and "ValueError" in r.failures[0].error
    assert len(r.series("n_stranded")) == 2, "a failed point must not enter the curve"
    assert "1 of 3 settings failed" in r.table()


def test_a_sweep_leaves_the_analysis_as_it_found_it():
    """Otherwise every number after a sweep is about the last setting it happened to try
    -- and nothing announces it."""
    a = ReachAnalysis(device=DEV, budget=0.7)
    before = a.setup
    a.sweep("budget", [0.1, 0.2, 0.3])
    assert a.setup == before, (a.setup, before)


def test_sweeping_an_undeclared_key_raises_before_running_anything():
    a = ReachAnalysis(device=DEV)
    with pytest.raises(KeyError):
        a.sweep("buget", [0.1, 0.2])


def test_a_nested_key_can_be_swept():
    b = BudgetAnalysis(device=DEV)
    r = b.sweep("scale.shuttle", [1.0, 0.5])
    assert all(p.ok for p in r.points), [p.error for p in r.points]
    assert r.points[0].data["total_error"] > r.points[1].data["total_error"]


# -- the physics the contract is carrying -------------------------------------------

def test_the_budget_sweep_is_linear_in_the_channel_scale():
    """R16 makes summed error linear in each channel's contribution, so the sweep must
    come out as a straight line. A curve here would mean the scaling is leaking into
    something else."""
    b = BudgetAnalysis(device=DEV)
    r = b.sweep("scale.junction", [1.0, 0.75, 0.5, 0.25, 0.0])
    ys = [v for _, v in r.series("total_error")]
    steps = [ys[i] - ys[i + 1] for i in range(len(ys) - 1)]
    assert all(s == pytest.approx(steps[0], rel=1e-9) for s in steps), steps


def test_the_slope_of_the_sweep_is_the_channels_own_derivative():
    """The sweep and the error budget are two routes to the same number; if they
    disagree, one of them is wrong and the table is not an instrument."""
    b = BudgetAnalysis(device=DEV)
    full = b.run()
    junction = full["report"].channels
    direct = next(c for c in junction if c.name == "junction").error

    r = b.sweep("scale.junction", [1.0, 0.0])
    at1, at0 = (v for _, v in r.series("total_error"))
    assert at1 - at0 == pytest.approx(direct, rel=1e-9), (at1 - at0, direct)


def test_two_channel_scales_compose_rather_than_overwrite():
    """`scale` nests one `_Scaled` per channel. Because the error is linear in each,
    scaling both must move the total by exactly the sum of the separate moves -- a
    mis-nested wrapper (one silently replacing the other) fails this."""
    base = BudgetAnalysis(device=DEV).run()["total_error"]
    only_j = BudgetAnalysis(device=DEV, scale={"junction": 0.5}).run()["total_error"]
    only_s = BudgetAnalysis(device=DEV, scale={"shuttle": 0.5}).run()["total_error"]
    both = BudgetAnalysis(
        device=DEV, scale={"junction": 0.5, "shuttle": 0.5}).run()["total_error"]
    assert base - both == pytest.approx((base - only_j) + (base - only_s), rel=1e-9)


def test_zero_stranded_on_a_device_that_cools_everywhere_is_not_a_verdict():
    """Every device in `arch/` cools at every site, so `stranded` is 0 by construction at
    any budget. A bare 0 reads as 'the coolers are well placed'; it means there is no
    placement to get wrong, and the report has to say which."""
    a = ReachAnalysis(device=DEV)
    r = a.sweep("budget", [0.01, 1.0, 100.0])
    assert all(p.data["n_stranded"] == 0 for p in r.points)
    rep = reach_report(load(DEV), corrected_model())
    assert any("not evidence that the placement is good" in n for n in rep.notes), rep.notes


def test_the_stranded_metric_does_discriminate_where_cooling_is_sparse():
    """The negative control for the test above: the metric is not simply always zero."""
    from test_reach import rail                          # noqa: PLC0415

    arch = rail(33, 16)
    a = ReachAnalysis(device=arch)
    r = a.sweep("budget", [0.5, 1.0, 2.0, 4.0])
    got = [p.data["n_stranded"] for p in r.points]
    assert got[0] > 0, "a tight budget must strand something on sparse cooling"
    assert got[-1] == 0, "a loose enough budget must rescue everything"
    assert got == sorted(got, reverse=True), got
    assert not any(math.isnan(p.data["stranded_fraction"]) for p in r.points)
