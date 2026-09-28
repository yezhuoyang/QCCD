"""Logical error rate by sampling and decoding, with an honest interval.

Sampling is adaptive: batches are drawn until `max_errors` logical failures are seen or
`max_shots` is reached (or `time_limit_s` runs out), which spends shots where the rate is
low and stops early where it is high.  The interval is Wilson's 95 % score interval, which
stays valid at zero failures -- the case a good device lands in -- where the normal
approximation collapses to [0, 0].

The decoder is chosen from the circuit's error model:

* **pymatching** when stim can decompose every error into graph-like pieces (at most two
  detectors each): the surface and repetition codes under circuit noise.  Minimum-weight
  perfect matching is fast and near-optimal there.
* **BP-OSD** (ldpc's `BpOsdDecoder`, product-sum BP with combination-sweep OSD) otherwise:
  flagged circuits, BB codes, anything with hyperedges.  Slower, so identical syndromes
  are decoded once, and the all-zero syndrome is never decoded at all.

`seed` fixes stim's sampler.  Stim documents its samples as reproducible for one seed,
one stim version and one machine architecture, so a report records the stim version; an
official number is computed on the server and a local one is an estimate.
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass

__all__ = ["LerEstimate", "estimate_ler", "wilson", "per_round", "choose_decoder"]

_Z95 = 1.959963984540054


@dataclass
class LerEstimate:
    ler: float
    lo: float
    hi: float
    shots: int
    errors: int
    per_round: float
    per_round_lo: float
    per_round_hi: float
    rounds: int
    decoder: str
    seconds: float
    seed: int | None
    stim_version: str
    stopped: str = ""                  # "max_errors" | "max_shots" | "time_limit" | "exact"

    def to_json(self) -> dict:
        return asdict(self)


def wilson(errors: int, shots: int, z: float = _Z95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion; (0, 1) with no shots."""
    if shots <= 0:
        return 0.0, 1.0
    p = errors / shots
    z2 = z * z
    denom = 1.0 + z2 / shots
    centre = (p + z2 / (2 * shots)) / denom
    half = z * math.sqrt(p * (1 - p) / shots + z2 / (4 * shots * shots)) / denom
    lo = 0.0 if errors == 0 else max(0.0, centre - half)       # exact at the ends, not
    hi = 1.0 if errors == shots else min(1.0, centre + half)   # a rounding residue
    return lo, hi


def per_round(ler: float, rounds: int) -> float:
    """1 − (1 − LER)^(1/rounds): the per-round rate that compounds to `ler`."""
    if rounds <= 1:
        return ler
    if ler >= 1.0:
        return 1.0
    return 1.0 - (1.0 - ler) ** (1.0 / rounds)


def choose_decoder(circuit) -> tuple[str, object]:
    """("pymatching", decomposed DEM) when every error is graph-like, else ("bposd", DEM).

    This sees only the error model.  A code whose qubits sit in three checks (Steane, BB)
    can still decompose into graph edges and decode badly under matching; a caller that
    knows its code says so (`MemoryExperiment.matchable`, which `evaluate_memory` uses).
    """
    try:
        dem = circuit.detector_error_model(decompose_errors=True)
    except ValueError:
        return "bposd", circuit.detector_error_model(decompose_errors=False)
    for inst in dem.flattened():
        if inst.type != "error":
            continue
        piece = 0
        for t in inst.targets_copy():
            if t.is_separator():
                piece = 0
            elif t.is_relative_detector_id():
                piece += 1
                if piece > 2:
                    return "bposd", circuit.detector_error_model(decompose_errors=False)
    return "pymatching", dem


class _BpOsd:
    """ldpc's BP-OSD on the DEM's check matrix, with a syndrome cache."""

    def __init__(self, dem, *, osd_order: int = 7, max_iter: int | None = None):
        import numpy as np
        from ldpc import BpOsdDecoder
        from scipy.sparse import csc_matrix

        rows, cols, lrows, lcols, priors = [], [], [], [], []
        j = 0
        for inst in dem.flattened():
            if inst.type != "error":
                continue
            p = inst.args_copy()[0]
            dets = set()
            obs = set()
            for t in inst.targets_copy():
                if t.is_relative_detector_id():
                    dets ^= {t.val}
                elif t.is_logical_observable_id():
                    obs ^= {t.val}
            for d in dets:
                rows.append(d)
                cols.append(j)
            for o in obs:
                lrows.append(o)
                lcols.append(j)
            priors.append(p)
            j += 1
        nd, no = dem.num_detectors, dem.num_observables
        self.H = csc_matrix((np.ones(len(rows), dtype=np.uint8), (rows, cols)), shape=(nd, j))
        self.L = csc_matrix((np.ones(len(lrows), dtype=np.uint8), (lrows, lcols)), shape=(no, j))
        self.no = no
        self.dec = BpOsdDecoder(self.H, error_channel=priors,
                                max_iter=max_iter or max(nd, 100),
                                bp_method="product_sum", osd_method="osd_cs",
                                osd_order=osd_order)
        self.cache: dict[bytes, object] = {}

    def decode_batch(self, det):
        import numpy as np

        out = np.zeros((det.shape[0], self.no), dtype=np.uint8)
        for s in np.flatnonzero(det.any(axis=1)):
            row = det[s].astype(np.uint8)
            key = np.packbits(row).tobytes()
            pred = self.cache.get(key)
            if pred is None:
                e = self.dec.decode(row)
                pred = (self.L @ e) % 2
                self.cache[key] = pred
            out[s] = pred
        return out


def estimate_ler(circuit, *, decoder: str = "auto", max_shots: int = 1_000_000,
                 max_errors: int = 100, batch: int = 10_000, seed: int | None = None,
                 rounds: int = 1, time_limit_s: float | None = None) -> LerEstimate:
    """Sample `circuit`, decode, and count shots where ANY observable is predicted wrong."""
    import numpy as np
    import stim

    t0 = time.time()
    if circuit.num_observables == 0:
        raise ValueError("the circuit declares no observable: there is no logical error to count")
    if decoder not in ("auto", "pymatching", "bposd"):
        raise ValueError(f"decoder must be auto, pymatching or bposd, not {decoder!r}")
    if decoder == "auto":
        name, dem = choose_decoder(circuit)
    elif decoder == "pymatching":
        name, dem = "pymatching", circuit.detector_error_model(decompose_errors=True)
    else:
        name, dem = "bposd", circuit.detector_error_model(decompose_errors=False)

    def result(errors: int, shots: int, stopped: str, lo=None, hi=None) -> LerEstimate:
        ler = errors / shots if shots else 0.0
        if lo is None:
            lo, hi = wilson(errors, shots)
        return LerEstimate(ler=ler, lo=lo, hi=hi, shots=shots, errors=errors,
                           per_round=per_round(ler, rounds),
                           per_round_lo=per_round(lo, rounds),
                           per_round_hi=per_round(hi, rounds), rounds=rounds, decoder=name,
                           seconds=round(time.time() - t0, 3), seed=seed,
                           stim_version=stim.__version__, stopped=stopped)

    if dem.num_errors == 0:
        # no error mechanism reaches any detector or observable: the rate is exactly 0
        return result(0, 0, "exact", 0.0, 0.0)

    if name == "pymatching":
        import pymatching

        matcher = pymatching.Matching.from_detector_error_model(dem)
        decode = matcher.decode_batch
    else:
        decode = _BpOsd(dem).decode_batch

    sampler = circuit.compile_detector_sampler(seed=seed)
    shots = errors = 0
    stopped = "max_shots"
    while shots < max_shots:
        k = min(batch, max_shots - shots)
        det, obs = sampler.sample(k, separate_observables=True)
        pred = decode(det)
        errors += int(np.count_nonzero(np.any(pred != obs, axis=1)))
        shots += k
        if errors >= max_errors:
            stopped = "max_errors"
            break
        if time_limit_s is not None and time.time() - t0 >= time_limit_s:
            stopped = "time_limit"
            break
    return result(errors, shots, stopped)
