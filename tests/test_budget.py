"""Shares with a derivative, and a conservation check that can fail.

`1747 quanta = 267 shuttling + 1336 junction + 144 dock` is a true sentence and not an
instrument.  `error_budget` turns it into "junction transits are 76% of the infidelity
heating causes; halving them buys 587 in summed gate error", which is a sentence you can
act on -- the shape qiskit-metal reports as an energy-participation ratio.

**The derivative is exact.**  `gate_error` is ``eps0 + slope * nbar`` (R16), so summed
error is linear in the n-bar carried into gates, and n-bar is a linear accumulation of
per-move charges.  Two points therefore determine the line exactly: there is no step size
to pick and no truncation error, and `test_the_derivative_is_linear_not_approximate`
asserts that rather than assuming it.

**Every channel is metered where it is actually charged.**  The first version of `_Scaled`
rescaled the charges a model RETURNS, which reaches three channels and misses `anomalous`
-- the replay accrues that one from elapsed time at a rate the model owns.  So the naive
answer for a channel carrying 3,741 quanta was 0.0, which is not a small error but a claim
that it does not matter.  `_Scaled` now also scales `anomalous_per_us`, and
`test_anomalous_is_measured_at_its_own_rate` pins the number against an independent
measurement rather than against itself.

**The sum is therefore a real check.**  Four independently measured derivatives have no
reason to add up to the heating error unless the decomposition is right; they do, to 4e-15
relative.  That test is only meaningful while nothing was attributed by difference -- the
conservation fallback would make it true by construction -- so it asserts that too.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.analysis import budget as budget_mod  # noqa: E402
from qccd.analysis import error_budget  # noqa: E402
from qccd.analysis.budget import _Scaled  # noqa: E402
from qccd.arch import load  # noqa: E402
from qccd.compile.programs import build  # noqa: E402
from qccd.cost import corrected_model  # noqa: E402
from qccd.verify import replay  # noqa: E402

ARCH = ROOT / "arch"


@pytest.fixture(scope="module")
def deck():
    arch = load(ARCH / "ring144_24v.arch.json")
    return arch, build(arch, "deck"), corrected_model()


@pytest.fixture(scope="module")
def report(deck):
    arch, prog, model = deck
    return error_budget(prog, arch, model)


def test_the_channels_sum_to_the_heating_error(report):
    """The conservation check. Each channel's error is measured independently, by its own
    two-point derivative, with no arithmetic tying them together -- so there is no reason
    for them to add up to the whole unless the decomposition is correct."""
    assert not any(c.by_difference for c in report.channels), (
        "a channel attributed by difference makes this sum true by construction; the "
        "check is only meaningful when every channel was measured on its own")
    attributed = sum(c.error for c in report.channels if c.attributable)
    assert attributed == pytest.approx(report.heating_error, rel=1e-9), (
        attributed, report.heating_error)
    assert report.total_error == pytest.approx(report.floor_error + report.heating_error)


def test_shares_sum_to_one_over_the_attributable_channels(report):
    shares = [c.share for c in report.channels if c.attributable and c.quanta]
    assert shares, "nothing was attributable"
    assert sum(shares) == pytest.approx(1.0, rel=1e-9)


def test_junction_transits_dominate_the_shipped_schedule(report):
    """The project's central finding, now with a derivative attached."""
    by = {c.name: c for c in report.channels}
    assert by["junction"].attributable
    assert by["junction"].share > 0.5, by["junction"].share
    assert by["junction"].error > by["shuttle"].error > by["split_merge"].error
    assert by["junction"].halving_buys == pytest.approx(0.5 * by["junction"].error)


def test_the_derivative_is_linear_not_approximate(deck):
    """The claim the whole method rests on: scaling a channel moves the summed error
    along a straight line, so two points give the exact slope. Take a THIRD point and
    confirm it lies on that line."""
    arch, prog, model = deck
    base = replay(prog, arch, model, check_rules=False).gate_error_sum
    two = replay(prog, arch, _Scaled(model, "junction", 2.0),
                 check_rules=False).gate_error_sum
    three = replay(prog, arch, _Scaled(model, "junction", 3.0),
                   check_rules=False).gate_error_sum
    slope = two - base
    assert three - base == pytest.approx(2.0 * slope, rel=1e-9), (
        "the summed error is not linear in the channel, so a two-point derivative "
        "would be an approximation and this module claims it is exact")


class _RateOnly:
    """Scale ONLY the anomalous heating rate, leaving every returned charge alone.

    An independent route to the same number: `_Scaled` is what the module under test uses,
    so checking its answer with itself would prove nothing.
    """

    def __init__(self, inner, k):
        self._inner, self._k = inner, float(k)

    def __getattr__(self, item):
        return getattr(self._inner, item)

    def anomalous_per_us(self, arch):
        return self._inner.anomalous_per_us(arch) * self._k


def test_anomalous_is_measured_at_its_own_rate(deck, report):
    """The bug this file mostly exists for. `anomalous` is not returned as a charge -- the
    replay accrues it from elapsed time -- so rescaling the charges a model returns leaves
    it untouched and reports 0.0 for a channel carrying 3,741 quanta. Pin it against an
    independent measurement, not against itself."""
    arch, prog, model = deck
    anom = next(c for c in report.channels if c.name == "anomalous")
    assert anom.quanta > 0, "this test is meaningless if the channel is empty"
    assert anom.attributable, "the channel must not be reported as unmeasurable"
    assert anom.error > 0.0, "an unreachable channel must never report 0.0 error"

    base = replay(prog, arch, model, check_rules=False)
    two = replay(prog, arch, _RateOnly(model, 2.0), check_rules=False)
    assert two.quanta_components["anomalous"] == pytest.approx(
        2.0 * base.quanta_components["anomalous"], rel=1e-12)
    for other in ("shuttle", "junction", "split_merge"):
        assert two.quanta_components[other] == base.quanta_components[other], other
    independent = two.gate_error_sum - base.gate_error_sum
    assert anom.error == pytest.approx(independent, rel=1e-9), (anom.error, independent)


def _blind(*channels):
    """A `_Scaled` that silently fails to reach the named channels -- the old bug, on
    purpose, so the fallback path can be tested."""

    class Blind(_Scaled):
        def anomalous_per_us(self, arch):
            if "anomalous" in channels:
                return self._inner.anomalous_per_us(arch)
            return super().anomalous_per_us(arch)

        def _rescale(self, charge):
            if self._channel in channels:
                return charge
            return super()._rescale(charge)

    return Blind


def test_one_unreachable_channel_is_recovered_by_conservation(deck, report, monkeypatch):
    """The fallback. Blind the scaling to `anomalous` and it must come back by
    difference -- and land on the number the direct measurement gets, which is the only
    evidence that conservation is a derivation rather than a plausible-looking guess."""
    arch, prog, model = deck
    monkeypatch.setattr(budget_mod, "_Scaled", _blind("anomalous"))
    r = error_budget(prog, arch, model)
    anom = next(c for c in r.channels if c.name == "anomalous")
    assert anom.by_difference, "the unreachable channel should have been derived"
    assert anom.attributable
    direct = next(c for c in report.channels if c.name == "anomalous").error
    assert anom.error == pytest.approx(direct, rel=1e-6), (anom.error, direct)
    assert any("conservation" in n for n in r.notes), r.notes


def test_two_unreachable_channels_stay_unknown_rather_than_being_split(deck, monkeypatch):
    """Conservation gives ONE unknown, not two. With a joint residual there is no honest
    way to divide it, so both must report unknown -- a plausible zero, or a split down the
    middle, is far more dangerous than a visible gap."""
    arch, prog, model = deck
    monkeypatch.setattr(budget_mod, "_Scaled", _blind("anomalous", "split_merge"))
    r = error_budget(prog, arch, model)
    for name in ("anomalous", "split_merge"):
        c = next(x for x in r.channels if x.name == name)
        assert c.quanta > 0
        assert c.attributable is False, name
        assert math.isnan(c.error), f"{name} must not report 0.0 error"
        assert math.isnan(c.share)
    assert any("unattributed" in n for n in r.notes), r.notes
    # the ones that were reached still divide their own total cleanly
    assert sum(c.share for c in r.channels if c.attributable and c.quanta) == \
        pytest.approx(1.0, rel=1e-9)


def test_a_programme_with_no_gates_says_so(deck):
    """There is no infidelity to attribute, and zeros would look like an answer."""
    arch = load(ARCH / "cyclone_base.arch.json")
    r = error_budget(build(arch, "oddeven"), arch, corrected_model())
    assert r.n_gate_pairs == 0
    assert any("runs no gates" in n for n in r.notes), r.notes
    assert all(c.error == 0.0 for c in r.channels)


def test_scaling_one_channel_leaves_the_others_alone(deck):
    """`_Scaled` is supposed to be surgical. If it moved a second channel the derivative
    would be attributing that one's contribution too."""
    arch, prog, model = deck
    base = replay(prog, arch, model, check_rules=False).quanta_components
    got = replay(prog, arch, _Scaled(model, "junction", 2.0),
                 check_rules=False).quanta_components
    assert got["junction"] == pytest.approx(2.0 * base["junction"], rel=1e-9)
    for other in ("shuttle", "split_merge", "anomalous"):
        assert got[other] == pytest.approx(base[other], rel=1e-9), other
