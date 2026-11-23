"""Every estimate says what decoded it, and the beam decoder is asked for, never assumed.

`LerEstimate.decoder_config` names the library, its version and each setting that affects the
answer; `describe_decoder` writes the same description for a stored row that kept only the
decoder's name and its budget.  The beam decoder (IonQ's BeamSearchDecoder, docs/decoders.md) is
not a dependency: without it the project works and asking for it is a clear error.  Its wrapper is
tested here against a stand-in library with the real one's interface, and against the real one
when `QCCD_BEAM_DECODER` names a built checkout.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

stim = pytest.importorskip("stim")
pytest.importorskip("pymatching")
pytest.importorskip("ldpc")

from qccd.qec import _LER_BUDGET_KEYS, bb, get_experiment, ideal_stim  # noqa: E402
from qccd.qec import decode as D  # noqa: E402
from qccd.qec.decode import (BEAM_ENV, BEAM_KEYS, BEAM_PRESETS, DecoderNotInstalled, LerEstimate,  # noqa: E402
                             beam_settings, choose_decoder, describe_decoder, estimate_ler)

FEW = {"max_shots": 300, "max_errors": 10**9}


@pytest.fixture(scope="module")
def bb18():
    exp = bb("bb18", rounds=1)
    return ideal_stim(exp, 3e-3)


@pytest.fixture
def no_beam(monkeypatch):
    """A machine without the beam decoder, whatever this one has."""
    monkeypatch.delenv(BEAM_ENV, raising=False)
    monkeypatch.delitem(sys.modules, "beam_search_decoder", raising=False)
    real = importlib.import_module

    def absent(name, *a, **k):
        if name == "beam_search_decoder":
            raise ModuleNotFoundError("No module named 'beam_search_decoder'")
        return real(name, *a, **k)

    monkeypatch.setattr(importlib, "import_module", absent)


# ------------------------------------------------------------------ what ran is recorded


def test_matching_records_its_library_and_version():
    est = estimate_ler(ideal_stim(get_experiment("surface3"), 1e-3), seed=1, **FEW)
    cfg = est.decoder_config
    assert est.decoder == cfg["decoder"] == "pymatching"
    assert cfg["library"] == "pymatching" and cfg["version"] == importlib.metadata.version("pymatching")
    assert cfg["label"] == f"Minimum-weight perfect matching (pymatching {cfg['version']})"
    assert est.unsolved == 0 and 0.0 <= est.decode_seconds <= est.seconds
    assert json.loads(json.dumps(est.to_json()))["decoder_config"] == cfg


def test_bposd_records_every_setting_it_ran_with(bb18):
    est = estimate_ler(bb18, decoder="bposd", seed=2, bp_max_iter=30, **FEW)
    cfg = est.decoder_config
    assert (cfg["library"], cfg["version"]) == ("ldpc", importlib.metadata.version("ldpc"))
    assert (cfg["bp_method"], cfg["max_iter"], cfg["osd_method"], cfg["osd_order"]) == ("product_sum", 30, "osd_cs", 7)
    assert cfg["max_iter_rule"] == "bp_max_iter" and cfg["schedule"] == "parallel"
    assert cfg["label"] == f"BP-OSD (ldpc {cfg['version']}: product-sum BP, 30 iterations; OSD-CS order 7)"
    # without a cap, the iterations are the detector count, at least 100: the number is recorded
    free = estimate_ler(bb18, decoder="bposd", seed=2, **FEW).decoder_config
    assert free["max_iter"] == max(bb18.num_detectors, 100) and free["max_iter_rule"] == "max(detectors, 100)"


def test_a_noiseless_circuit_still_says_its_decoder():
    est = estimate_ler(ideal_stim(get_experiment("rep3"), 0.0))
    assert est.stopped == "exact" and est.decoder_config["decoder"] == est.decoder


# ------------------------------------------------------------------ describing a stored row


def test_describe_decoder_is_pure_and_reads_an_old_row():
    """A row stored before the configuration was recorded kept the name and the budget."""
    old = {"ler": 0.008, "lo": 0.007, "hi": 0.009, "shots": 30000, "errors": 242, "per_round": 0.00135,
           "per_round_lo": 0.0012, "per_round_hi": 0.0015, "rounds": 6, "decoder": "bposd", "seconds": 303.6,
           "seed": 20261029, "stim_version": "1.15.0", "stopped": "max_errors"}
    est = LerEstimate(**old)                           # an old row still loads
    assert est.decoder_config == {} and est.unsolved == 0 and est.decode_seconds == 0.0
    budget = {"batch": 10000, "bp_max_iter": 30, "decoder": "bposd", "max_errors": 200, "max_shots": 2000000}
    cfg = describe_decoder(est.decoder, budget, versions={"ldpc": "2.4.1"})
    assert cfg["label"] == "BP-OSD (ldpc 2.4.1: product-sum BP, 30 iterations; OSD-CS order 7)"
    assert cfg == describe_decoder(est.decoder, budget, versions={"ldpc": "2.4.1"})
    # no cap in the budget: the detector count decides, and an unknown count is said, not guessed
    assert describe_decoder("bposd", {}, n_detectors=504)["max_iter"] == 504
    assert describe_decoder("bposd", {}, n_detectors=24)["max_iter"] == 100
    unknown = describe_decoder("bposd")
    assert unknown["max_iter"] is None and unknown["version"] is None
    assert unknown["label"] == ("BP-OSD (ldpc: product-sum BP, as many iterations as detectors, at least 100; "
                                "OSD-CS order 7)")
    assert describe_decoder("pymatching", {"decoder": "pymatching"})["label"] == \
        "Minimum-weight perfect matching (pymatching)"
    assert describe_decoder("something-else")["label"] == "something-else"


def test_published_rows_load_and_are_described_from_what_they_kept():
    """Goal 3's published rows: each `ler` still loads, and its decoder is described from the
    row's own name and budget (the rows from before the configuration was recorded have no more)."""
    files = sorted((ROOT / "goals" / "goal3" / "results").glob("*.jsonl"))
    if not files:
        pytest.skip("no published rows in this checkout")
    seen = set()
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line) if line.strip() else {}
            if not row.get("ler"):
                continue
            est = LerEstimate(**row["ler"])
            budget = row.get("ler_budget") or {}
            cfg = est.decoder_config or describe_decoder(est.decoder, budget)
            assert cfg["decoder"] == est.decoder and cfg["label"], (path.name, row["point"])
            if est.decoder == "bposd" and budget.get("bp_max_iter") and not est.decoder_config:
                assert f"{budget['bp_max_iter']} iterations; OSD-CS order 7" in cfg["label"]
            seen.add(est.decoder)
    assert seen >= {"bposd", "pymatching"}


def test_describe_decoder_matches_what_ran(bb18):
    est = estimate_ler(bb18, decoder="bposd", seed=2, bp_max_iter=30, **FEW)
    told = describe_decoder("bposd", {"bp_max_iter": 30}, bb18.num_detectors,
                            {"ldpc": importlib.metadata.version("ldpc")})
    assert told == est.decoder_config


# ------------------------------------------------------------------ the beam decoder's settings


def test_beam_settings_are_the_papers_for_each_width():
    assert beam_settings({}) == {"beam_width": 8, "max_rounds": 10, "initial_iters": 30, "iters_per_round": 20,
                                 "num_results": 1}
    assert beam_settings({"beam_width": 32}) == {"beam_width": 32, **BEAM_PRESETS[32]}
    assert beam_settings({"beam_width": 64}) == {"beam_width": 64, "max_rounds": 20, "initial_iters": 40,
                                                 "iters_per_round": 30, "num_results": 1}
    assert beam_settings({"beam_width": 16})["initial_iters"] == 30       # the width-8 row
    assert beam_settings({"beam_width": 64, "beam_num_results": 32})["num_results"] == 32
    for bad in ({"beam_width": 0}, {"beam_width": 8.0}, {"beam_width": True}, {"beam_max_rounds": -1}):
        with pytest.raises(ValueError):
            beam_settings(bad)
    cfg = describe_decoder("beam", {"beam_width": 32}, versions={"beam_search_decoder": "5100dc0520672ed2"})
    assert cfg["label"] == ("Beam search (IonQ BeamSearchDecoder 5100dc0: width 32; min-sum BP, 40 iterations "
                            "then up to 10 rounds of 30)")
    assert (cfg["beam_width"], cfg["bp_method"], cfg["license"]) == (32, "minimum_sum", "CC BY-NC-SA 4.0")


def test_the_budget_knows_the_beam_keys():
    assert set(BEAM_KEYS) <= set(_LER_BUDGET_KEYS)


def test_beam_is_asked_for_never_chosen(bb18):
    assert choose_decoder(bb18)[0] == "bposd"
    assert estimate_ler(bb18, seed=1, **FEW).decoder == "bposd"
    with pytest.raises(ValueError, match="beam_width"):
        estimate_ler(bb18, decoder="bposd", beam_width=8, **FEW)
    with pytest.raises(ValueError, match="decoder must be"):
        estimate_ler(bb18, decoder="osd", **FEW)


def test_without_the_library_beam_is_a_clear_error_and_the_rest_works(bb18, no_beam):
    with pytest.raises(DecoderNotInstalled) as exc:
        estimate_ler(bb18, decoder="beam", seed=1, **FEW)
    assert "docs/decoders.md" in str(exc.value) and BEAM_ENV in str(exc.value)
    assert isinstance(exc.value, ImportError)
    assert estimate_ler(bb18, decoder="bposd", seed=1, **FEW).decoder == "bposd"


# ------------------------------------------------------------------ the wrapper, on a stand-in

_STAND_IN = '''
import numpy as np


class BeamSearchDecoder:
    """The real library's interface; it "decodes" by flipping the first error of each lit detector."""

    def __init__(self, pcm, **kw):
        assert sorted(kw) == ["beam_width", "error_channel", "initial_iters", "iters_per_round", "max_rounds",
                              "num_results"], kw
        self.pcm = pcm.tocsr()
        self.n = pcm.shape[1]
        for k, v in kw.items():
            setattr(self, k, v)

    def decode(self, row):
        e = np.zeros(self.n, dtype=np.uint8)
        if self.beam_width >= 32:                       # the wide beam "solves" nothing
            return e
        for d in np.flatnonzero(row):
            e[self.pcm.indices[self.pcm.indptr[d]]] ^= 1
        return e
'''


@pytest.fixture
def stand_in(tmp_path, monkeypatch):
    """A checkout with the real one's layout: decoder/beam_search_decoder, src_cpp, .git."""
    pkg = tmp_path / "decoder" / "beam_search_decoder"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(_STAND_IN, encoding="utf-8")
    (tmp_path / "decoder" / "src_cpp").mkdir()
    (tmp_path / "decoder" / "src_cpp" / "beam_search.hpp").write_bytes(b"// stand-in\r\n")
    git = tmp_path / ".git" / "refs" / "heads"
    git.mkdir(parents=True)
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git / "main").write_text("0123456789abcdef0123456789abcdef01234567\n", encoding="utf-8")
    monkeypatch.setenv(BEAM_ENV, str(tmp_path))
    monkeypatch.delitem(sys.modules, "beam_search_decoder", raising=False)
    before = list(sys.path)
    yield tmp_path
    sys.modules.pop("beam_search_decoder", None)
    assert sys.path == before                          # the checkout is not left on the path


def test_beam_runs_through_the_library_and_records_it(bb18, stand_in):
    est = estimate_ler(bb18, decoder="beam", seed=3, **FEW)
    cfg = est.decoder_config
    assert est.decoder == cfg["decoder"] == "beam" and est.shots == 300
    assert cfg["commit"] == "0123456789abcdef0123456789abcdef01234567"
    import hashlib
    assert cfg["source_sha256"] == hashlib.sha256(b"// stand-in\n").hexdigest()     # line endings normalised
    assert {k: cfg[k] for k in ("beam_width", "max_rounds", "initial_iters", "iters_per_round", "num_results")} == \
        beam_settings({})
    assert cfg["label"].startswith("Beam search (IonQ BeamSearchDecoder 0123456: width 8; min-sum BP, 30 ")
    assert list(cfg)[-1] == "label" and json.loads(json.dumps(est.to_json()))["decoder_config"] == cfg
    # a correction that misses the syndrome is counted, and scored by what was returned
    wide = estimate_ler(bb18, decoder="beam", beam_width=32, beam_num_results=2, seed=3, **FEW)
    det = bb18.compile_detector_sampler(seed=3).sample(300)
    assert wide.unsolved == int(det.any(axis=1).sum()) > 0
    assert (wide.decoder_config["beam_width"], wide.decoder_config["num_results"]) == (32, 2)


def test_an_unbuilt_checkout_says_so(bb18, tmp_path, monkeypatch, no_beam):
    monkeypatch.setenv(BEAM_ENV, str(tmp_path))
    with pytest.raises(DecoderNotInstalled, match="holds no decoder/beam_search_decoder"):
        estimate_ler(bb18, decoder="beam", **FEW)


# ------------------------------------------------------------------ the real library, when built


def _real_checkout():
    root = os.environ.get(BEAM_ENV)
    built = root and any(p.suffix in (".pyd", ".so")
                         for p in Path(root).glob("decoder/beam_search_decoder/_beam_search_decoder*"))
    return root if built else None


@pytest.mark.skipif(_real_checkout() is None, reason=f"{BEAM_ENV} names no built BeamSearchDecoder checkout")
def test_the_real_beam_decoder_decodes_a_bb_code(bb18):
    sys.modules.pop("beam_search_decoder", None)
    bp = estimate_ler(bb18, decoder="bposd", seed=2, max_shots=1000, max_errors=10**9)
    for width in (8, 32, 64):
        est = estimate_ler(bb18, decoder="beam", beam_width=width, seed=2, max_shots=1000, max_errors=10**9)
        assert est.decoder_config["beam_width"] == width and est.decoder_config["commit"]
        assert est.shots == bp.shots and est.errors < 4 * max(bp.errors, 10), (width, est.errors, bp.errors)
        assert est.unsolved <= est.errors
