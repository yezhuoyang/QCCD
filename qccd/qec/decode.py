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

A third decoder is never chosen, only asked for (`decoder="beam"`):

* **beam** -- IonQ's BP-guided beam search for quantum LDPC codes (Ye, Wecker, Delfosse,
  arXiv:2512.07057), their own code, on the same check matrix as BP-OSD.  It is not a
  dependency: docs/decoders.md says where to get it and how to build it, and asking for it
  without it raises `DecoderNotInstalled`.  `beam_width` 8, 32 or 64 takes the paper's
  settings for that width (`BEAM_PRESETS`).

Every estimate says what decoded it: `LerEstimate.decoder_config` names the library, its
version and each setting that affects the answer, and `describe_decoder` writes the same
description for a stored row that kept only the decoder's name and its budget.

`seed` fixes stim's sampler.  Stim documents its samples as reproducible for one seed,
one stim version and one machine architecture, so a report records the stim version; an
official number is computed on the server and a local one is an estimate.
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from typing import Mapping

__all__ = ["LerEstimate", "estimate_ler", "wilson", "per_round", "choose_decoder",
           "describe_decoder", "beam_settings", "DecoderNotInstalled", "DECODERS",
           "BEAM_PRESETS", "BEAM_KEYS", "BEAM_ENV"]

_Z95 = 1.959963984540054

DECODERS = ("auto", "pymatching", "bposd", "beam")

# BP-OSD as this project has always run it (ldpc's BpOsdDecoder)
_BP_METHOD = "product_sum"
_OSD_METHOD = "osd_cs"
_OSD_ORDER = 7

# the beam decoder: where it comes from, and the paper's settings per width (its Table 1:
# beam8_230iters, beam32_340iters, beam64_640iters)
BEAM_SOURCE = "https://github.com/ionq-publications/BeamSearchDecoder"
BEAM_PAPER = "arXiv:2512.07057"
BEAM_LICENSE = "CC BY-NC-SA 4.0"
BEAM_ENV = "QCCD_BEAM_DECODER"          # the checkout, when it is not on the import path
BEAM_PRESETS = {
    8: {"max_rounds": 10, "initial_iters": 30, "iters_per_round": 20, "num_results": 1},
    32: {"max_rounds": 10, "initial_iters": 40, "iters_per_round": 30, "num_results": 1},
    64: {"max_rounds": 20, "initial_iters": 40, "iters_per_round": 30, "num_results": 1},
}
BEAM_KEYS = ("beam_width", "beam_max_rounds", "beam_initial_iters", "beam_iters_per_round",
             "beam_num_results")


class DecoderNotInstalled(ImportError):
    """A decoder was asked for by name and its library is not on this machine."""


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
    # what decoded it (`describe_decoder`): library, version, every setting that affects the
    # answer, and a one-line `label`.  Empty on a row stored before 2026-11-23.
    decoder_config: dict = field(default_factory=dict)
    decode_seconds: float = 0.0        # of `seconds`, the part spent inside the decoder
    # shots whose returned correction does not reproduce the syndrome (a beam search that ran
    # out of rounds); they are counted by what the decoder returned.  BP-OSD and matching
    # always return a correction that does.
    unsolved: int = 0

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
    The beam decoder is never chosen here: a budget asks for it by name.
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


# ------------------------------------------------------------------ what a decoder is


def beam_settings(budget: Mapping | None = None) -> dict:
    """The beam decoder's settings under `budget`: `beam_width` (default 8) and, for each
    setting the budget leaves out, the paper's value for that width -- for a width the paper
    does not tabulate, the row of the largest tabulated width not above it."""
    b = dict(budget or {})
    width = 8 if b.get("beam_width") is None else b["beam_width"]
    row = BEAM_PRESETS[max((w for w in BEAM_PRESETS if isinstance(width, int) and w <= width),
                           default=8)]
    out = {"beam_width": width}
    for k in ("max_rounds", "initial_iters", "iters_per_round", "num_results"):
        v = b.get("beam_" + k)
        out[k] = row[k] if v is None else v
    for k, v in out.items():
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            name = k if k == "beam_width" else "beam_" + k
            raise ValueError(f"{name} must be a positive integer, not {v!r}")
    return out


def _label(cfg: Mapping) -> str:
    """One line a reader can check a number against."""
    name = cfg.get("decoder")
    ver = cfg.get("version")
    if name == "pymatching":
        return f"Minimum-weight perfect matching (pymatching{' ' + ver if ver else ''})"
    if name == "bposd":
        it = cfg.get("max_iter")
        its = (f"{it} iterations" if it is not None
               else "as many iterations as detectors, at least 100")
        bp = {"product_sum": "product-sum", "minimum_sum": "min-sum"}.get(
            str(cfg.get("bp_method")), str(cfg.get("bp_method")))
        osd = str(cfg.get("osd_method", "")).upper().replace("_", "-")
        return (f"BP-OSD (ldpc{' ' + ver if ver else ''}: {bp} BP, {its}; "
                f"{osd} order {cfg.get('osd_order')})")
    if name == "beam":
        commit = cfg.get("commit")
        results = cfg.get("num_results")
        best = f"; best of {results} solutions" if results and results > 1 else ""
        return (f"Beam search (IonQ BeamSearchDecoder{' ' + commit[:7] if commit else ''}: "
                f"width {cfg.get('beam_width')}; min-sum BP, {cfg.get('initial_iters')} iterations "
                f"then up to {cfg.get('max_rounds')} rounds of {cfg.get('iters_per_round')}{best})")
    if name == "auto":
        return "chosen from the error model: matching when every error is graph-like, else BP-OSD"
    return str(name)


def describe_decoder(name: str, budget: Mapping | None = None, n_detectors: int | None = None,
                     versions: Mapping | None = None) -> dict:
    """What decoder `name` is under `budget`: its library and version, every setting that
    affects the answer, and a one-line `label`, such as
    "BP-OSD (ldpc 2.4.1: product-sum BP, 30 iterations; OSD-CS order 7)".

    Pure: it reads only its arguments, so it also describes a stored row that kept nothing
    but the decoder's name (`ler.decoder`) and its budget.  `n_detectors` is the circuit's
    detector count, which BP-OSD's iteration cap falls back to when the budget has no
    `bp_max_iter`; `versions` is the row's `tools` (`ldpc`, `pymatching`, and for the beam
    decoder `beam_search_decoder`: its source commit, `beam_search_decoder_sha256`: the digest
    of its algorithm file), where a missing entry reads as unknown.
    """
    b = dict(budget or {})
    v = dict(versions or {})
    if name == "pymatching":
        cfg = {"decoder": "pymatching", "library": "pymatching", "version": v.get("pymatching"),
               "method": "minimum-weight perfect matching",
               "error_model": "stim detector error model, errors decomposed into graph-like pieces"}
    elif name == "bposd":
        cap = b.get("bp_max_iter")
        cfg = {"decoder": "bposd", "library": "ldpc", "version": v.get("ldpc"),
               "bp_method": _BP_METHOD, "schedule": "parallel",
               "max_iter": cap or (max(n_detectors, 100) if n_detectors is not None else None),
               "max_iter_rule": "bp_max_iter" if cap else "max(detectors, 100)",
               "osd_method": _OSD_METHOD, "osd_order": _OSD_ORDER,
               "error_model": "stim detector error model, hyperedges kept"}
    elif name == "beam":
        cfg = {"decoder": "beam", "library": "BeamSearchDecoder", "source": BEAM_SOURCE,
               "commit": v.get("beam_search_decoder"),
               "source_sha256": v.get("beam_search_decoder_sha256"),
               "paper": BEAM_PAPER, "license": BEAM_LICENSE,
               **beam_settings(b), "bp_method": "minimum_sum", "schedule": "parallel",
               "error_model": "stim detector error model, hyperedges kept"}
    else:
        cfg = {"decoder": name}
    cfg["label"] = _label(cfg)
    return cfg


def _installed(dist: str) -> str | None:
    from importlib import metadata

    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return None


# ------------------------------------------------------------------ the beam decoder's library


def _beam_library():
    """(the `BeamSearchDecoder` class, {"commit", "source_sha256"}) of IonQ's library.

    Found on the import path (`beam_search_decoder`, the package in the checkout's `decoder`
    directory), else in the checkout `QCCD_BEAM_DECODER` names.  The commit is read from the
    checkout's `.git`, and `source_sha256` is the digest of `decoder/src_cpp/beam_search.hpp`
    (line endings normalised): the file that is the algorithm.
    """
    import importlib
    import os
    import sys
    from pathlib import Path

    def load():
        return importlib.import_module("beam_search_decoder")

    try:
        mod = load()
    except ImportError as first:
        root = os.environ.get(BEAM_ENV)
        pkg = Path(root) / "decoder" if root else None
        if pkg is None or not (pkg / "beam_search_decoder").is_dir():
            raise DecoderNotInstalled(
                "the beam decoder (decoder: \"beam\") is not installed: it is IonQ's "
                f"BeamSearchDecoder ({BEAM_SOURCE}), which this project does not ship.  Build it "
                f"and set {BEAM_ENV} to its checkout, as docs/decoders.md says"
                + (f" ({BEAM_ENV}={root!r} holds no decoder/beam_search_decoder)" if root else "")
                + f".  [{first}]") from first
        sys.path.insert(0, str(pkg))
        try:
            mod = load()
        except ImportError as exc:
            raise DecoderNotInstalled(
                f"the beam decoder at {BEAM_ENV}={root!r} is not built for this Python "
                f"(run `python setup.py build_ext --inplace` in its decoder directory; "
                f"docs/decoders.md).  [{exc}]") from exc
        finally:
            sys.path.remove(str(pkg))
    info = {"commit": None, "source_sha256": None}
    try:
        checkout = Path(mod.__file__).resolve().parents[2]
        info["commit"] = _git_head(checkout)
        src = checkout / "decoder" / "src_cpp" / "beam_search.hpp"
        if src.is_file():
            import hashlib

            info["source_sha256"] = hashlib.sha256(
                src.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    except (OSError, IndexError):
        pass
    return mod.BeamSearchDecoder, info


def _git_head(checkout) -> str | None:
    """The commit a checkout is at, from its `.git` directory (no git needed)."""
    git = checkout / ".git"
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head or None
        ref = head.split(":", 1)[1].strip()
        if (git / ref).is_file():
            return (git / ref).read_text(encoding="utf-8").strip() or None
        for line in (git / "packed-refs").read_text(encoding="utf-8").splitlines():
            if line.endswith(" " + ref):
                return line.split(" ", 1)[0]
    except OSError:
        pass
    return None


# ------------------------------------------------------------------ decoders on the check matrix


def _check_matrices(dem):
    """(H, L, priors) of a detector error model: one column per error mechanism, its
    detectors in H and its observables in L."""
    import numpy as np
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
    H = csc_matrix((np.ones(len(rows), dtype=np.uint8), (rows, cols)), shape=(nd, j))
    L = csc_matrix((np.ones(len(lrows), dtype=np.uint8), (lrows, lcols)), shape=(no, j))
    return H, L, priors


class _MatrixDecoder:
    """A decoder on the DEM's check matrix, with a syndrome cache: identical syndromes are
    decoded once, and the all-zero syndrome never."""

    check_solution = False             # count corrections that miss the syndrome (`unsolved`)

    def __init__(self, dem):
        self.H, self.L, self.priors = _check_matrices(dem)
        self.no = dem.num_observables
        self.cache: dict[bytes, tuple] = {}
        self.unsolved = 0
        self.times: list[float] | None = None    # a benchmark sets []: seconds per decoded syndrome

    def correction(self, row):
        raise NotImplementedError

    def decode_batch(self, det):
        import numpy as np

        out = np.zeros((det.shape[0], self.no), dtype=np.uint8)
        for s in np.flatnonzero(det.any(axis=1)):
            row = det[s].astype(np.uint8)
            key = np.packbits(row).tobytes()
            hit = self.cache.get(key)
            if hit is None:
                t0 = time.perf_counter()
                e = np.asarray(self.correction(row), dtype=np.uint8)
                if self.times is not None:
                    self.times.append(time.perf_counter() - t0)
                solved = not self.check_solution or not np.any((self.H @ e) % 2 != row)
                hit = self.cache[key] = ((self.L @ e) % 2, solved)
            out[s] = hit[0]
            if not hit[1]:
                self.unsolved += 1
        return out


class _BpOsd(_MatrixDecoder):
    """ldpc's BP-OSD: product-sum BP, then combination-sweep OSD."""

    def __init__(self, dem, *, osd_order: int = _OSD_ORDER, max_iter: int | None = None):
        from ldpc import BpOsdDecoder

        super().__init__(dem)
        self.budget = {"bp_max_iter": max_iter}
        self.nd = dem.num_detectors
        self.dec = BpOsdDecoder(self.H, error_channel=self.priors,
                                max_iter=max_iter or max(self.nd, 100),
                                bp_method=_BP_METHOD, osd_method=_OSD_METHOD,
                                osd_order=osd_order)
        self.correction = self.dec.decode

    def config(self) -> dict:
        cfg = describe_decoder("bposd", self.budget, self.nd, {"ldpc": _installed("ldpc")})
        # what the library says it is running, not what it was asked for
        cfg.update(max_iter=int(self.dec.max_iter), bp_method=str(self.dec.bp_method),
                   schedule=str(self.dec.schedule), osd_method=str(self.dec.osd_method).lower(),
                   osd_order=int(self.dec.osd_order))
        cfg["label"] = _label(cfg)
        return cfg


class _Beam(_MatrixDecoder):
    """IonQ's beam search decoder, as its library runs it.  A search that finds no solution
    within its rounds returns its first BP pass's hard decision, which need not reproduce
    the syndrome: such shots are counted (`unsolved`) and scored by what was returned."""

    check_solution = True

    def __init__(self, dem, settings: Mapping):
        cls, self.info = _beam_library()
        super().__init__(dem)
        self.settings = dict(settings)
        s = self.settings
        # by keyword: the library's base class reads its settings from the keywords
        self.dec = cls(self.H, error_channel=list(self.priors), max_rounds=s["max_rounds"],
                       beam_width=s["beam_width"], num_results=s["num_results"],
                       initial_iters=s["initial_iters"], iters_per_round=s["iters_per_round"])
        self.correction = self.dec.decode

    def config(self) -> dict:
        s = self.settings
        cfg = describe_decoder("beam", {"beam_width": s["beam_width"],
                                        **{"beam_" + k: s[k] for k in s if k != "beam_width"}},
                               versions={"beam_search_decoder": self.info["commit"],
                                         "beam_search_decoder_sha256": self.info["source_sha256"]})
        cfg.update(beam_width=int(self.dec.beam_width), max_rounds=int(self.dec.max_rounds),
                   initial_iters=int(self.dec.initial_iters),
                   iters_per_round=int(self.dec.iters_per_round),
                   num_results=int(self.dec.num_results))
        cfg["label"] = _label(cfg)
        return cfg


def estimate_ler(circuit, *, decoder: str = "auto", max_shots: int = 1_000_000,
                 max_errors: int = 100, batch: int = 10_000, seed: int | None = None,
                 rounds: int = 1, time_limit_s: float | None = None,
                 bp_max_iter: int | None = None, beam_width: int | None = None,
                 beam_max_rounds: int | None = None, beam_initial_iters: int | None = None,
                 beam_iters_per_round: int | None = None,
                 beam_num_results: int | None = None) -> LerEstimate:
    """Sample `circuit`, decode, and count shots where ANY observable is predicted wrong.

    `bp_max_iter` caps BP-OSD's belief propagation (default: the detector count, at least 100).
    A board fixes it in its release: on bb18's two-copy memory, 30 decodes 2.7x faster and
    misses the same shots (35 vs 36 of 1000).

    `decoder="beam"` is IonQ's beam search (docs/decoders.md); `beam_width` (default 8) and
    the other `beam_*` settings default to the paper's for that width (`beam_settings`), and
    belong to that decoder alone.  The estimate's `decoder_config` records what ran."""
    import numpy as np
    import stim

    t0 = time.time()
    if circuit.num_observables == 0:
        raise ValueError("the circuit declares no observable: there is no logical error to count")
    if decoder not in DECODERS:
        raise ValueError(f"decoder must be auto, pymatching, bposd or beam, not {decoder!r}")
    beam_budget = {"beam_width": beam_width, "beam_max_rounds": beam_max_rounds,
                   "beam_initial_iters": beam_initial_iters,
                   "beam_iters_per_round": beam_iters_per_round,
                   "beam_num_results": beam_num_results}
    given = sorted(k for k, v in beam_budget.items() if v is not None)
    if given and decoder != "beam":
        raise ValueError(f"{', '.join(given)} belong(s) to decoder \"beam\", not {decoder!r}")
    if decoder == "auto":
        name, dem = choose_decoder(circuit)
    elif decoder == "pymatching":
        name, dem = "pymatching", circuit.detector_error_model(decompose_errors=True)
    else:
        name, dem = decoder, circuit.detector_error_model(decompose_errors=False)
    if name == "beam":
        settings = beam_settings(beam_budget)
        _beam_library()                 # asked for and not installed: say so before sampling
    config: dict = {}
    spent = {"decode": 0.0, "unsolved": 0}

    def result(errors: int, shots: int, stopped: str, lo=None, hi=None) -> LerEstimate:
        ler = errors / shots if shots else 0.0
        if lo is None:
            lo, hi = wilson(errors, shots)
        return LerEstimate(ler=ler, lo=lo, hi=hi, shots=shots, errors=errors,
                           per_round=per_round(ler, rounds),
                           per_round_lo=per_round(lo, rounds),
                           per_round_hi=per_round(hi, rounds), rounds=rounds, decoder=name,
                           seconds=round(time.time() - t0, 3), seed=seed,
                           stim_version=stim.__version__, stopped=stopped,
                           decoder_config=config, decode_seconds=round(spent["decode"], 3),
                           unsolved=spent["unsolved"])

    if dem.num_errors == 0:
        # no error mechanism reaches any detector or observable: the rate is exactly 0
        versions = {"pymatching": _installed("pymatching"), "ldpc": _installed("ldpc")}
        if name == "beam":
            lib = _beam_library()[1]
            versions.update(beam_search_decoder=lib["commit"],
                            beam_search_decoder_sha256=lib["source_sha256"])
        config = describe_decoder(name, {"bp_max_iter": bp_max_iter, **beam_budget},
                                  dem.num_detectors, versions)
        return result(0, 0, "exact", 0.0, 0.0)

    if name == "pymatching":
        import pymatching

        matcher = pymatching.Matching.from_detector_error_model(dem)
        decode = matcher.decode_batch
        config = describe_decoder("pymatching", versions={"pymatching": _installed("pymatching")})
        dec = None
    else:
        dec = _Beam(dem, settings) if name == "beam" else _BpOsd(dem, max_iter=bp_max_iter)
        decode = dec.decode_batch
        config = dec.config()

    sampler = circuit.compile_detector_sampler(seed=seed)
    shots = errors = 0
    stopped = "max_shots"
    while shots < max_shots:
        k = min(batch, max_shots - shots)
        det, obs = sampler.sample(k, separate_observables=True)
        t1 = time.perf_counter()
        pred = decode(det)
        spent["decode"] += time.perf_counter() - t1
        errors += int(np.count_nonzero(np.any(pred != obs, axis=1)))
        shots += k
        if errors >= max_errors:
            stopped = "max_errors"
            break
        if time_limit_s is not None and time.time() - t0 >= time_limit_s:
            stopped = "time_limit"
            break
    if dec is not None:
        spent["unsolved"] = dec.unsolved
    return result(errors, shots, stopped)
