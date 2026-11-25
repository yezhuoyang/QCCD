#!/usr/bin/env python3
"""Reproduce one leaderboard data point: sample the noisy circuit, decode, count.

    python run.py                 the published experiment: same seed, same budget, same decoder
    python run.py --shots 20000   a quick run with fewer shots (compared by its 95% interval)

This script needs no part of the QCCD project.  It reads the two files beside it,

    circuit.stim       the noisy stim circuit: the hardware program of this point with the
                       board's noise model applied, and the experiment's detectors and observables
    experiment.json    the decoder and every one of its parameters, the seed, the shot budget,
                       the stopping rule, and the published result to compare with

and needs stim, numpy and the decoder's package (pymatching, or ldpc with scipy):
`pip install -r requirements.txt` installs the versions the published number was made with.

WHAT IS RUN (the same procedure as the project's `qccd/qec/decode.py::estimate_ler`):

* The detector error model of the circuit is taken from stim: for PyMatching with
  `decompose_errors=True` (every error as graph edges), for BP-OSD with `decompose_errors=False`.
* One sampler is made from the circuit with the recorded seed.  Batches of `batch` shots are
  drawn from it until `max_errors` logical errors have been seen (checked after each whole
  batch) or `max_shots` shots have been drawn.
* A shot is a logical error when ANY observable is predicted wrong.
* PyMatching: minimum-weight perfect matching (`pymatching.Matching.from_detector_error_model`).
  BP-OSD: `ldpc.BpOsdDecoder` on the error model's check matrix with product-sum belief
  propagation and combination-sweep ordered-statistics decoding, the parameters as recorded;
  a syndrome seen before is not decoded again, and the all-zero syndrome is never decoded.
* The rate per round is 1 - (1 - LER)^(1/rounds); the interval is Wilson's 95% score interval.

WHAT "REPRODUCED" MEANS.  stim documents a seeded sample as reproducible for one stim version
on one machine architecture.  With the same versions on the same kind of machine the counts come
out identical; on another machine or version they are another sample of the same distribution.
So the verdict is "identical" when errors and shots equal the published ones, and "consistent"
when the published rate lies inside the 95% interval of this run, or the two intervals overlap.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
Z95 = 1.959963984540054


def wilson(errors: int, shots: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion; (0, 1) with no shots."""
    if shots <= 0:
        return 0.0, 1.0
    p = errors / shots
    z2 = z * z
    denom = 1.0 + z2 / shots
    centre = (p + z2 / (2 * shots)) / denom
    half = z * math.sqrt(p * (1 - p) / shots + z2 / (4 * shots * shots)) / denom
    lo = 0.0 if errors == 0 else max(0.0, centre - half)
    hi = 1.0 if errors == shots else min(1.0, centre + half)
    return lo, hi


def per_round(ler: float, rounds: int) -> float:
    """1 - (1 - LER)^(1/rounds): the per-round rate that compounds to `ler`."""
    if rounds <= 1:
        return ler
    if ler >= 1.0:
        return 1.0
    return 1.0 - (1.0 - ler) ** (1.0 / rounds)


class BpOsd:
    """ldpc's BP-OSD on the error model's check matrix, with a syndrome cache."""

    def __init__(self, dem, *, max_iter, bp_method: str, osd_method: str, osd_order: int):
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
        #: the recorded cap on belief propagation, or (none recorded) the detector count, at least 100
        self.max_iter = max_iter or max(nd, 100)
        self.dec = BpOsdDecoder(self.H, error_channel=priors, max_iter=self.max_iter,
                                bp_method=bp_method, osd_method=osd_method, osd_order=osd_order)
        self.cache: dict = {}

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


def versions() -> dict:
    from importlib import metadata

    out = {}
    for dist in ("stim", "pymatching", "ldpc", "numpy", "scipy"):
        try:
            out[dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            out[dist] = None
    return out


def run(folder: Path = HERE, *, shots: int | None = None, seed: int | None = None,
        progress=None) -> dict:
    """Sample and decode `folder`'s circuit as its experiment.json says; the counts and rates.

    `shots` caps the budget for a quick run; `seed` replaces the recorded seed (another sample).
    """
    import numpy as np
    import stim

    spec = json.loads((folder / "experiment.json").read_text(encoding="utf-8"))
    circuit = stim.Circuit.from_file(str(folder / "circuit.stim"))
    dec, samp, pub = spec["decoder"], spec["sampling"], (spec.get("published") or {}).get("ler") or {}
    name, par = dec["name"], dec.get("parameters") or {}
    rounds = int(spec["experiment"]["rounds"])
    max_shots, max_errors, batch = int(samp["max_shots"]), int(samp["max_errors"]), int(samp["batch"])
    use_seed = samp["seed"] if seed is None else seed
    full = shots is None and seed is None
    if pub.get("stopped") == "time_limit" and pub.get("shots"):
        # the published run was stopped by its wall clock, after this many shots: draw as many
        max_shots = int(pub["shots"])
    if shots is not None:
        max_shots = min(max_shots, int(shots))

    t0 = time.time()
    if circuit.num_observables == 0:
        raise SystemExit("the circuit declares no observable: there is no logical error to count")
    if name == "pymatching":
        dem = circuit.detector_error_model(decompose_errors=True)
    elif name == "bposd":
        dem = circuit.detector_error_model(decompose_errors=False)
    else:
        raise SystemExit(f"experiment.json names a decoder this script does not know: {name!r}")

    n_shots = errors = 0
    stopped = "max_shots"
    max_iter = None
    if dem.num_errors == 0:
        # no error mechanism reaches any detector or observable: the rate is exactly 0
        stopped = "exact"
    else:
        if name == "pymatching":
            import pymatching

            decode = pymatching.Matching.from_detector_error_model(dem).decode_batch
        else:
            bp = BpOsd(dem, max_iter=par.get("bp_max_iter"), bp_method=par["bp_method"],
                       osd_method=par["osd_method"], osd_order=int(par["osd_order"]))
            decode, max_iter = bp.decode_batch, bp.max_iter
        sampler = circuit.compile_detector_sampler(seed=use_seed)
        while n_shots < max_shots:
            k = min(batch, max_shots - n_shots)
            det, obs = sampler.sample(k, separate_observables=True)
            pred = decode(det)
            errors += int(np.count_nonzero(np.any(pred != obs, axis=1)))
            n_shots += k
            if progress:
                progress(errors, n_shots, time.time() - t0)
            if errors >= max_errors:
                stopped = "max_errors"
                break

    ler = errors / n_shots if n_shots else 0.0
    lo, hi = (0.0, 0.0) if stopped == "exact" else wilson(errors, n_shots)
    out = {"errors": errors, "shots": n_shots, "ler": ler, "lo": lo, "hi": hi,
           "per_round": per_round(ler, rounds), "per_round_lo": per_round(lo, rounds),
           "per_round_hi": per_round(hi, rounds), "rounds": rounds, "decoder": name,
           "bp_max_iter_used": max_iter, "seed": use_seed, "stopped": stopped,
           "seconds": round(time.time() - t0, 3), "detectors": circuit.num_detectors,
           "observables": circuit.num_observables, "versions": versions(), "full_budget": full}
    out["verdict"] = verdict(out, pub)
    return out


def verdict(got: dict, pub: dict) -> str:
    """identical | consistent | different | nothing published."""
    if not pub or pub.get("shots") is None:
        return "nothing published"
    if got["full_budget"] and got["errors"] == pub["errors"] and got["shots"] == pub["shots"]:
        return "identical"
    inside = got["lo"] <= pub["ler"] <= got["hi"]
    overlap = got["lo"] <= pub["hi"] and pub["lo"] <= got["hi"]
    return "consistent" if (inside or overlap) else "different"


def _plain_console() -> None:
    """A title may carry a dash or an accent the terminal's code page lacks: print it anyway."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Reproduce this leaderboard point's logical error rate.")
    ap.add_argument("--shots", type=int, default=None,
                    help="draw at most this many shots (a quick run); default: the published budget")
    ap.add_argument("--seed", type=int, default=None,
                    help="another seed: an independent sample of the same experiment")
    ap.add_argument("--json", default=None, metavar="FILE", help="also write the result as JSON to FILE")
    ap.add_argument("--quiet", action="store_true", help="no progress lines")
    a = ap.parse_args(argv)
    _plain_console()

    spec = json.loads((HERE / "experiment.json").read_text(encoding="utf-8"))
    pub = (spec.get("published") or {}).get("ler") or {}
    samp, dec = spec["sampling"], spec["decoder"]
    print(f"{spec['board']['title']}  --  {spec['entry']['title']}")
    gs = spec.get("gate_set")             # the hardware program's native gate set (a zip from before says none)
    if gs:
        print(f"gate set  {gs.get('label') or gs['name']}: the circuit.stim pulses' set; its entangling pulse is "
              "noised as the two-qubit gate")
    print(f"decoder   {dec['label']}")
    print(f"budget    up to {int(samp['max_shots']):,} shots or {int(samp['max_errors'])} logical errors, "
          f"batches of {int(samp['batch']):,}, seed {samp['seed']}"
          + (f"  (this run: at most {a.shots:,} shots)" if a.shots is not None else "")
          + (f"  (this run: seed {a.seed})" if a.seed is not None else ""))

    def progress(errors, shots, secs):
        if not a.quiet:
            print(f"  ... {errors} errors in {shots:,} shots ({secs:.0f} s)", file=sys.stderr, flush=True)

    got = run(HERE, shots=a.shots, seed=a.seed, progress=progress)
    r = got["rounds"]
    print(f"this run  {got['errors']} logical errors in {got['shots']:,} shots  (stopped: {got['stopped']}, "
          f"{got['seconds']:.1f} s)")
    print(f"          LER per shot   {got['ler']:.6g}   95% interval {got['lo']:.6g} to {got['hi']:.6g}")
    print(f"          LER per round  {got['per_round']:.6g}   95% interval {got['per_round_lo']:.6g} to "
          f"{got['per_round_hi']:.6g}   ({r} round{'s' if r != 1 else ''})")
    if pub.get("shots") is not None:
        print(f"published {pub['errors']} logical errors in {int(pub['shots']):,} shots")
        print(f"          LER per shot   {pub['ler']:.6g}   95% interval {pub['lo']:.6g} to {pub['hi']:.6g}")
        print(f"          LER per round  {pub['per_round']:.6g}   95% interval {pub['per_round_lo']:.6g} to "
              f"{pub['per_round_hi']:.6g}")
    tools = (spec.get("published") or {}).get("tools") or {}
    mine = got["versions"]
    diff = [f"{k} {mine.get(k)} here, {v} published" for k, v in tools.items()
            if v and mine.get(k) and mine.get(k) != v and k in ("stim", dec.get("package"))]
    if diff:
        print("versions  " + "; ".join(diff) + "  (another version draws another sample)")
    words = {
        "identical": "REPRODUCED EXACTLY: the same errors in the same shots as published.",
        "consistent": "REPRODUCED within the 95% interval: the published rate is consistent with this run "
                      "(a seeded sample differs between machines and versions; see the README).",
        "different": "NOT REPRODUCED: the published rate is outside this run's 95% interval and the "
                     "intervals do not overlap.",
        "nothing published": "No published rate to compare with.",
    }
    print("verdict   " + words[got["verdict"]])
    if a.json:
        Path(a.json).write_text(json.dumps(got, indent=1), encoding="utf-8")
    return 1 if got["verdict"] == "different" else 0


if __name__ == "__main__":
    raise SystemExit(main())
