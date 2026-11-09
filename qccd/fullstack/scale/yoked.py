"""Yoked cold storage (docs/FULLSTACK_PLAN.md, slice S10 step 1): a 2D yoked surface code's failure
per outer round from its inner patches' complementary gaps, the way Gidney, Newman, Brooks and Jones
estimate it ("Yoked surface codes", arXiv:2312.04522, section 4; their code and statistics are CC BY
4.0 at zenodo.org/records/10277397).

The inner patch.  A d x d surface patch, decoded by matching, also reports its *signed gap*: the
weight of the best correction in the other logical class minus the weight of the best one in the true
class, in dB (positive: the patch decoder is right with that confidence; negative: it is wrong).  The
gap is sampled over 10d-round memories (`signed_gaps`) and kept as a histogram {integer dB: shots}.

From a 10d-round histogram to N rounds (the paper's two approximations):
  * the gap over N rounds is the smallest of N / (10d) draws: P(G <= g) = 1 - (1 - F_10d(g))^(N/10d);
  * a patch with gap G is wrong with the frequency observed at G, or, where no failure was observed,
    with likelihood ratio 10^(-0.9 G / 10) (the paper's decoder is "slightly overconfident").

The outer code.  The W x W 2D parity check code (FormalRV's `make_yoked_code.py`, the paper's
[[W^2, W^2 - 4W + 2, 4]]): a patch's logical error flips its row check and its column check, so each
outer round is a complete bipartite graph between the W row checks and the W column checks whose
edges are the patches, weighted by their gaps; an outer check's measurement error is a timelike edge
between consecutive rounds.  Matching decodes it; the round fails when the patches left flipped by the
correction are not a product of the outer code's stabilizers of the other type (weight W each: in
practice any residual).  Two estimates of P(fail) per outer round, one basis each:
  * `rectangle_terms`, the estimate used: the leading failures counted exactly -- two wrong patches
    a rectangle's other two corners explain more cheaply, one wrong patch a three-patch path
    explains more cheaply, three wrong corners of a rectangle -- each by convolving the gap tables;
  * `outer`, a check: matching on the whole graph, sampling the number of failed edges and, given
    it, which fail and every gap.  It needs failures to see, so it undersamples low rates.
On Gidney et al.'s own data (`tools/yoked_study.py paper`) `rectangle_terms` meets their
gap-sampled points at W = 16 within 1.02 .. 1.15 wherever they counted 80 failures or more (W = 8:
0.89 .. 1.06); `outer` agrees where failures are many (d = 7, 8) and falls short below.

Both are one basis (one patch observable, one family of outer checks); the X and Z failures add.
Gidney et al.'s gap-sampled 2D points (their stats_extrapolation.csv) are "magic" memories, an X and
a Z observable each, so they compare with twice one basis.  Their fit at SI1000 noise (p = 1e-3), over
r_o outer rounds: p_L,2 ~ r_o r_i^4 n^2 50^-d / 200000, n = W^2 patches, r_i = 25 d W + 4 d.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

__all__ = ["GapTable", "Gaps", "outer_period", "paper_fit", "outer", "rectangle_terms", "signed_gaps",
           "with_gap_detector"]


def outer_period(d: int, w: int) -> int:
    """Inner rounds between a 2D yoke's outer checks: 25 d w + 4 d (arXiv:2312.04522, Fig. 7)."""
    return 25 * d * w + 4 * d


def paper_fit(d: int, w: int, r_o: int = 1) -> float:
    """The paper's 2D fit at SI1000 p = 1e-3, over r_o outer rounds."""
    return r_o * outer_period(d, w) ** 4 * (w * w) ** 2 * 50.0 ** (-d) / 200000


@dataclass(frozen=True)
class GapTable:
    """A patch's gap over some number of rounds: the absolute gap's distribution and, at each gap,
    the chance the patch decoder is wrong."""
    gaps: np.ndarray          # integer dB, ascending
    prob: np.ndarray          # P(|gap| = g)
    fail: np.ndarray          # P(wrong | |gap| = g)

    @classmethod
    def from_signed(cls, hist: dict[int, int]) -> "GapTable":
        """From a 10d-round signed histogram {dB: shots}; a zero gap is wrong half the time."""
        total = sum(hist.values())
        top = max(abs(g) for g in hist)
        gs = np.arange(0, top + 1)
        right = np.array([hist.get(g, 0) for g in gs], dtype=float)
        wrong = np.array([hist.get(-g, 0) for g in gs], dtype=float)
        right[0] = wrong[0] = hist.get(0, 0) / 2
        n = right + wrong
        keep = n > 0
        gs, right, wrong, n = gs[keep], right[keep], wrong[keep], n[keep]
        calibrated = 1 / (1 + 10 ** (0.9 * gs / 10))
        fail = np.where(wrong > 0, wrong / n, calibrated)
        return cls(gs, n / total, fail)

    def over(self, m: float) -> "GapTable":
        """The gap over m times the rounds: the smallest of m draws."""
        surv = 1 - np.cumsum(self.prob)
        surv = np.clip(surv, 0, 1) ** m
        cdf = 1 - surv
        return GapTable(self.gaps, np.diff(cdf, prepend=0.0), self.fail)

    @property
    def q(self) -> float:
        """P(the patch is wrong)."""
        return float(np.sum(self.prob * self.fail))

    def draw(self, rng, size, wrong: bool):
        """Gaps of patches known to be wrong (or right)."""
        w = self.prob * (self.fail if wrong else 1 - self.fail)
        return rng.choice(self.gaps, size=size, p=w / w.sum())

    def pmf(self, wrong: bool) -> np.ndarray:
        """The gap's distribution, dense on 0, 1, ... dB, for patches known to be wrong (or right)."""
        w = self.prob * (self.fail if wrong else 1 - self.fail)
        out = np.zeros(int(self.gaps[-1]) + 1)
        out[self.gaps] = w / w.sum()
        return out


def _cheaper(a: np.ndarray, b: np.ndarray) -> float:
    """P(X < Y) for independent X ~ a, Y ~ b on 0, 1, ... (ties count half)."""
    n = max(len(a), len(b))
    a, b = np.pad(a, (0, n - len(a))), np.pad(b, (0, n - len(b)))
    below = np.cumsum(a) - a / 2                 # P(X < y) + P(X = y) / 2
    return float(np.sum(b * below))


def rectangle_terms(space: GapTable, w: int) -> dict:
    """The leading failures of one outer round, exactly under their one cheaper alternative:
      pair  two wrong patches on a rectangle's diagonal, the other diagonal cheaper (P_c); or two in
            one row (column), some other row's (column's) pair under them cheaper
      one   one wrong patch, a three-patch path around a rectangle cheaper
      three three wrong corners of a rectangle, the fourth cheaper than the three (nearly always)
    Each is P(its patches are wrong) x the count x P(the alternative is cheaper); `total` their sum,
    per outer round, one basis."""
    ok, bad = space.pmf(False), space.pmf(True)
    ok2, bad2 = np.convolve(ok, ok), np.convolve(bad, bad)
    pc = _cheaper(ok2, bad2)
    p3 = _cheaper(np.convolve(ok2, ok), bad)
    pl = _cheaper(ok, np.convolve(bad2, bad))
    q = space.q
    rects = (w * (w - 1) / 2) ** 2
    diag = 2 * rects
    line = 2 * w * (w * (w - 1) / 2)
    pair = q * q * (diag * pc + line * (1 - (1 - pc) ** (w - 1)))
    one = w * w * q * (1 - (1 - p3) ** ((w - 1) ** 2))
    three = 4 * rects * q ** 3 * pl
    return {"P_c": pc, "P_3": p3, "P_L": pl, "pair": pair, "one": one, "three": three,
            "total": pair + one + three}


@lru_cache(maxsize=None)
def _graph(w: int, layers: int):
    """The outer decoding graph: nodes (layer, check) with checks 0..w-1 the rows and w..2w-1 the
    columns; spacelike edges (layer, patch) and timelike edges (layer boundary, check).  Returns the
    node x edge incidence, the edges' patches (-1 timelike) and the Z-type stabilizers' basis."""
    from scipy.sparse import csc_matrix
    nodes = layers * 2 * w
    rows, cols, patch = [], [], []
    e = 0
    for t in range(layers):
        for r in range(w):
            for c in range(w):
                rows += [t * 2 * w + r, t * 2 * w + w + c]
                cols += [e, e]
                patch.append(r * w + c)
                e += 1
    for t in range(layers - 1):
        for k in range(2 * w):
            rows += [t * 2 * w + k, (t + 1) * 2 * w + k]
            cols += [e, e]
            patch.append(-1)
            e += 1
    H = csc_matrix((np.ones(len(rows), dtype=np.uint8), (rows, cols)), shape=(nodes, e))
    patch = np.array(patch)
    F = csc_matrix((np.ones(int((patch >= 0).sum()), dtype=np.uint8),
                    (patch[patch >= 0], np.flatnonzero(patch >= 0))), shape=(w * w, e))
    return H, patch, F, _stabilizers(w)


def _stabilizers(w: int) -> list:
    """Row-reduced basis (pivot -> bit vector as a Python int) of the outer code's checks of the other
    type, in the W x W row-major patch order: FormalRV's Z bi-col and Z bi-row checks."""
    h = w // 2
    vecs = []
    for j in range(w):              # Z bi-col j
        par, c0 = j // h, 2 * (j % h)
        vecs.append(sum(1 << (r * w + c) for r in range(w) if r % 2 == par for c in (c0, c0 + 1)))
    for j in range(w):              # Z bi-row j
        r0, par = 2 * (j // 2), j % 2
        vecs.append(sum(1 << (r * w + c) for r in (r0, r0 + 1) for c in range(w) if c % 2 == par))
    basis: dict[int, int] = {}
    for v in vecs:
        while v:
            p = v.bit_length() - 1
            if p not in basis:
                basis[p] = v
                break
            v ^= basis[p]
    return basis


def _trivial(bits: np.ndarray, basis: dict) -> bool:
    v = int("".join("1" if b else "0" for b in bits[::-1]), 2)
    while v:
        p = v.bit_length() - 1
        if p not in basis:
            return False
        v ^= basis[p]
    return True


def outer(table10: GapTable, d: int, w: int, *, layers: int = 3, timelike_rounds: float | None = None,
          samples: int = 20000, max_failed: int = 4, seed: int = 1, ri: int | None = None) -> dict:
    """P(a 2D yoke of w x w patches of distance d fails) per outer round, from the patches' 10d-round
    gap table.  Each patch spends outer_period(d, w) rounds between checks; an outer check's
    measurement spans `timelike_rounds` patch-rounds (default 2 w d: the row of w patches and its
    workspace, d rounds); `ri` overrides the rounds between checks.  Returns the estimate and its
    parts."""
    import pymatching
    from scipy.stats import binom
    rng = np.random.default_rng(seed)
    ri = outer_period(d, w) if ri is None else ri
    tr = 2 * w * d if timelike_rounds is None else timelike_rounds
    space, time_ = table10.over(ri / (10 * d)), table10.over(tr / (10 * d))
    qs, qt = space.q, time_.q
    H, patch, F, basis = _graph(w, layers)
    ns, nt = int((patch >= 0).sum()), int((patch < 0).sum())
    mean = ns * qs + nt * qt
    parts = {}
    if mean > 2:                                    # many failures a block: sample them all
        fails = 0
        for _ in range(samples):
            wrong = np.concatenate([rng.random(ns) < qs, rng.random(nt) < qt])
            fails += _decode_fails(H, F, basis, wrong, space, time_, ns, nt, rng, pymatching)
        p_block = fails / samples
        parts["direct"] = {"samples": samples, "fails": int(fails)}
    else:                                           # k failed edges, k = 1 .. K, each sampled given k
        top = max(max_failed, min(10, math.ceil(mean + 5 * math.sqrt(mean) + 2)))
        p_block = 0.0
        for k in range(1, top + 1):
            split = np.array([binom.pmf(ks, ns, qs) * binom.pmf(k - ks, nt, qt) for ks in range(k + 1)])
            pk = float(split.sum())
            if pk < 1e-300:
                continue
            fails = 0
            for ks in rng.choice(k + 1, size=samples, p=split / pk):
                wrong = np.zeros(ns + nt, dtype=bool)
                wrong[rng.choice(ns, ks, replace=False)] = True
                if k - ks:
                    wrong[ns + rng.choice(nt, k - ks, replace=False)] = True
                fails += _decode_fails(H, F, basis, wrong, space, time_, ns, nt, rng, pymatching)
            parts[f"k{k}"] = {"p": pk, "fails": int(fails), "samples": samples}
            p_block += pk * fails / samples
    rect = rectangle_terms(space, w)
    return {"d": d, "w": w, "ri": ri, "q_space": qs, "q_time": qt, "mean_failed": mean,
            "p_block": p_block, "p_outer_round": p_block / layers, "rect": rect, "parts": parts}


def _decode_fails(H, F, basis, wrong, space, time_, ns, nt, rng, pymatching) -> bool:
    g = np.empty(ns + nt)
    for sl, tab, n0 in ((slice(0, ns), space, 0), (slice(ns, ns + nt), time_, ns)):
        wr = wrong[sl]
        gg = np.empty(wr.size)
        if wr.any():
            gg[wr] = tab.draw(rng, int(wr.sum()), True)
        gg[~wr] = tab.draw(rng, int((~wr).sum()), False)
        g[sl] = gg
    m = pymatching.Matching.from_check_matrix(H, weights=np.maximum(g, 1e-3), faults_matrix=F)
    syndrome = (H @ wrong.astype(np.uint8)) % 2
    corr = m.decode(syndrome)
    actual = (F @ wrong.astype(np.uint8)) % 2
    residual = (corr ^ actual).astype(bool)
    return residual.any() and not _trivial(residual, basis)


def with_gap_detector(circuit):
    """The memory circuit with its observable 0 also a detector, last: decoding with that detector
    flipped gives the best correction in the other logical class (the paper's gap collection)."""
    import stim
    c = circuit.flattened()
    out = stim.Circuit()
    m = 0
    obs = []
    for inst in c:
        if inst.name == "OBSERVABLE_INCLUDE" and int(inst.gate_args_copy()[0]) == 0:
            obs += [m + t.value for t in inst.targets_copy()]
        out.append(inst)
        m += inst.num_measurements
    out.append("DETECTOR", [stim.target_rec(i - m) for i in obs])
    return out


def signed_gaps(circuit, shots: int, batch: int = 20000, seed: int | None = None) -> dict[int, int]:
    """Sample a memory circuit made by `with_gap_detector`: {signed gap in integer dB: shots}."""
    import collections
    gap = Gaps(circuit)
    sampler = circuit.compile_detector_sampler(seed=seed)
    counts = collections.Counter()
    done = 0
    while done < shots:
        n = min(batch, shots - done)
        g = np.round(gap(sampler.sample(n, bit_packed=True))).astype(np.int64)
        u, c = np.unique(g, return_counts=True)
        for a, b in zip(u.tolist(), c.tolist()):
            counts[a] += b
        done += n
    return dict(counts)


class Gaps:
    """A gap circuit's matcher: called on bit-packed detection events (the gap detector last, at
    its sampled value), the signed gap of each shot in dB."""

    def __init__(self, circuit):
        import pymatching
        self.matcher = pymatching.Matching.from_detector_error_model(
            circuit.detector_error_model(decompose_errors=True))
        edge = next(iter(self.matcher.to_networkx().edges.values()))
        p = edge["error_probability"]
        self.db_per_w = -math.log10(p / (1 - p)) * 10 / edge["weight"]
        last = circuit.num_detectors - 1
        self.byte, self.mask = last // 8, np.uint8(1 << (last % 8))

    def __call__(self, dets: np.ndarray) -> np.ndarray:
        dets = dets.copy()
        kw = dict(return_weights=True, bit_packed_shots=True, bit_packed_predictions=True)
        _, w_true = self.matcher.decode_batch(dets, **kw)
        dets[:, self.byte] ^= self.mask
        _, w_other = self.matcher.decode_batch(dets, **kw)
        return (w_other - w_true) * self.db_per_w
