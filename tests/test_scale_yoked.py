"""Yoked cold storage's estimator (qccd/fullstack/scale/yoked.py, docs/FULLSTACK_PLAN.md S10 step 1)."""
import numpy as np
import pytest

stim = pytest.importorskip("stim")
pytest.importorskip("pymatching")

from qccd.fullstack.scale import yoked as Y


def test_outer_period_is_formalrv_generators():
    # make_yoked_code.py prints "outer period 2020" for d = 5, W = 16 and 2828 for d = 7
    assert Y.outer_period(5, 16) == 2020
    assert Y.outer_period(7, 16) == 2828
    assert Y.outer_period(11, 16) == 4444


def test_outer_checks_of_the_other_type():
    for w in (4, 8, 16):
        basis = Y._stabilizers(w)
        assert len(basis) == 2 * w - 1                # 4W - 2 independent checks, half of each type
    w = 16
    basis = Y._stabilizers(w)
    rect = np.zeros(w * w, dtype=bool)
    for r, c in ((0, 0), (0, 3), (5, 0), (5, 3)):
        rect[r * w + c] = True
    assert not Y._trivial(rect, basis)                # a rectangle is a logical at W = 16
    bicol = np.zeros(w * w, dtype=bool)
    for r in range(0, w, 2):
        bicol[r * w + 0] = bicol[r * w + 1] = True
    assert Y._trivial(bicol, basis)                   # Z bi-col 0 is a stabilizer


def test_gap_table():
    t = Y.GapTable.from_signed({-3: 2, 0: 10, 3: 50, 60: 938})
    assert t.prob.sum() == pytest.approx(1)
    # half the zero gaps, the observed share at 3 dB, the paper's calibration at 60 dB (none seen)
    assert t.q == pytest.approx((5 + 2 + 938 / (1 + 10 ** 5.4)) / 1000)
    assert Y.GapTable.from_signed({0: 10, 10: 990}).q == pytest.approx((5 + 990 / (1 + 10 ** 0.9)) / 1000)
    one = t.over(1)
    assert np.allclose(one.prob, t.prob)
    many = t.over(20)
    assert many.q > t.q and many.prob.sum() == pytest.approx(1)


def test_negative_gap_is_a_decoder_failure():
    c = stim.Circuit.generated("surface_code:rotated_memory_z", distance=3, rounds=3,
                               after_clifford_depolarization=0.01, before_measure_flip_probability=0.01)
    g = Y.with_gap_detector(c)
    assert g.num_detectors == c.num_detectors + 1
    sampler = g.compile_detector_sampler(seed=7)
    dets, obs = sampler.sample(20000, separate_observables=True)
    gaps = Y.Gaps(g)(np.packbits(dets, axis=1, bitorder="little"))
    import pymatching
    m = pymatching.Matching.from_detector_error_model(c.detector_error_model(decompose_errors=True))
    wrong = m.decode_batch(dets[:, :-1])[:, 0] != obs[:, 0]
    # the plain matcher merges parallel boundary edges the gap circuit keeps apart, so near-ties
    # can go either way; past 6 dB the sign is the decoder's verdict
    sure = np.abs(gaps) > 6
    assert wrong.sum() > 50
    assert np.array_equal(wrong[sure], gaps[sure] < 0)
    assert abs((gaps < 0).sum() + (gaps == 0).sum() / 2 - wrong.sum()) < 0.1 * wrong.sum()


def test_outer_counts_rectangles():
    t = Y.GapTable.from_signed({-2: 30, 0: 40, 2: 300, 6: 2000, 12: 20000, 20: 1_000_000})
    r = Y.outer(t, 3, 4, samples=300, ri=30)
    assert r["mean_failed"] < 2 and r["p_outer_round"] > 0
    rect = Y.rectangle_terms(t.over(1), 4)
    assert 0 < rect["P_c"] < 1 and rect["pair"] > 0
