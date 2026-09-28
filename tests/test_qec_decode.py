"""Sampling and decoding: a known circuit lands where the literature puts it.

The reference is stim's own generated rotated surface code (d = 3, 3 rounds, p = 1e-3),
whose logical error rate under matching is well known to be of order 1e-3; a seeded
estimate must land in a sane band around it.  The interval and the per-round conversion
are checked as arithmetic.  The two decoders are compared on a compiled program in
test_qec_extract.py, where a compiled program is at hand.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

stim = pytest.importorskip("stim")
pytest.importorskip("pymatching")
pytest.importorskip("ldpc")

from qccd.qec import get_experiment, ideal_stim  # noqa: E402
from qccd.qec.decode import choose_decoder, estimate_ler, per_round, wilson  # noqa: E402


@pytest.fixture(scope="module")
def textbook():
    return stim.Circuit.generated(
        "surface_code:rotated_memory_z", distance=3, rounds=3,
        after_clifford_depolarization=1e-3, before_measure_flip_probability=1e-3,
        after_reset_flip_probability=1e-3, before_round_data_depolarization=1e-3)


def test_textbook_surface_code_lands_in_a_sane_band(textbook):
    est = estimate_ler(textbook, seed=11, max_errors=60, max_shots=400_000, rounds=3)
    assert est.decoder == "pymatching"
    assert 2e-4 < est.ler < 3e-3, est
    assert est.lo <= est.ler <= est.hi
    assert est.per_round == pytest.approx(1 - (1 - est.ler) ** (1 / 3))
    assert est.stopped in ("max_errors", "max_shots")
    assert json.loads(json.dumps(est.to_json()))["seed"] == 11


def test_a_seed_reproduces_the_estimate(textbook):
    a = estimate_ler(textbook, seed=5, max_shots=20_000, max_errors=10**9)
    b = estimate_ler(textbook, seed=5, max_shots=20_000, max_errors=10**9)
    assert (a.errors, a.shots) == (b.errors, b.shots)


def test_wilson_interval():
    lo, hi = wilson(0, 1000)
    assert lo == 0.0 and 0.003 < hi < 0.004          # z²/(n+z²) ≈ 3.83e-3
    lo, hi = wilson(50, 1000)
    assert lo < 0.05 < hi and hi - lo < 0.03
    assert wilson(0, 0) == (0.0, 1.0)
    assert wilson(1000, 1000)[1] == 1.0


def test_per_round():
    assert per_round(0.19, 2) == pytest.approx(0.1)
    assert per_round(0.3, 1) == 0.3
    assert per_round(1.0, 5) == 1.0
    assert per_round(0.0, 5) == 0.0


def test_auto_picks_matching_only_for_graphlike_models():
    assert choose_decoder(ideal_stim(get_experiment("surface3"), 1e-3))[0] == "pymatching"
    assert choose_decoder(ideal_stim(get_experiment("bb18"), 1e-3))[0] == "bposd"


def test_bposd_decodes_a_bb_code():
    from qccd.qec import bb

    exp = bb("bb18", rounds=1)
    est = estimate_ler(ideal_stim(exp, 1e-3), seed=2, max_shots=1000, max_errors=10**9,
                       rounds=exp.rounds)
    assert est.decoder == "bposd" and 0.0 <= est.ler < 0.2


def test_a_noiseless_circuit_is_exactly_zero():
    est = estimate_ler(ideal_stim(get_experiment("rep3"), 0.0))
    assert (est.ler, est.lo, est.hi, est.stopped) == (0.0, 0.0, 0.0, "exact")


def test_a_circuit_without_observables_is_refused():
    with pytest.raises(ValueError):
        estimate_ler(stim.Circuit("M 0\nDETECTOR rec[-1]"))


def test_time_limit_stops_sampling(textbook):
    est = estimate_ler(textbook, seed=1, max_shots=10**9, max_errors=10**9, batch=1000,
                       time_limit_s=0.2)
    assert est.stopped == "time_limit" and est.shots >= 1000
    assert not math.isnan(est.hi)
