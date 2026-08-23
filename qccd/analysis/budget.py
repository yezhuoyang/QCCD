"""Where the infidelity actually goes, and what buys the most by fixing it.

We report itemized totals: *1747 quanta = 267 shuttling + 1336 junction + 144 dock*.
That is honest and it is not an instrument.  qiskit-metal reports **normalized shares with
a derivative** -- "EPR of substrate = 58.3%" -- and an architect can act on that.  This is
the same shape for a QCCD: each heating channel as an absolute, a share of the total gate
infidelity, and *the derivative* -- how much infidelity you buy back by halving it.

Why the derivative is exact rather than a finite difference
-----------------------------------------------------------
`CorrectedModel.gate_error` is ``eps0 + slope * nbar`` (R16), so the summed error over a
programme is::

    E = n_gates * eps0  +  slope * sum(nbar at each gate)

which is **linear** in the n-bar ions carry into gates.  And n-bar is itself a linear
accumulation of per-move charges.  So scaling one heating channel by `k` moves `E` along a
straight line, and two points determine it exactly -- there is no step size to choose and
no truncation error to apologise for.  `d(E)/d(channel)` here is the real derivative, not
an approximation of one.

What it costs
-------------
One extra replay per channel.  The tempting shortcut is to re-aggregate the per-instruction
charges already recorded, with no replay at all -- but the replay tracks the running n-bar
per ion as a SCALAR (`replay.py`'s `current`), so the split by channel at gate time is not
stored, and inventing it would mean either changing the hot loop that produces every number
this project has verified, or guessing.  A slow honest answer beats a fast invented one.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Mapping

from ..arch import Architecture
from ..cost.models import CostModel
from ..ir.tsir import TSIR

__all__ = ["Channel", "BudgetReport", "error_budget"]

#: the heating channels a replay separates
CHANNELS = ("shuttle", "junction", "split_merge", "gate", "anomalous")


@dataclass
class Channel:
    name: str
    quanta: float = 0.0
    #: this channel's share of the n-bar that reaches gates, 0..1
    share: float = 0.0
    #: total gate infidelity attributable to it
    error: float = 0.0
    #: d(total error) / d(scaling this channel), exact -- see the module docstring
    slope: float = 0.0
    #: what halving it buys, in total infidelity
    halving_buys: float = 0.0
    #: False when this channel's contribution cannot be isolated at all -- its numbers
    #: are NaN rather than 0.0, because 0.0 would be a claim
    attributable: bool = True
    #: True when the figure came from conservation rather than from rescaling: the
    #: channels partition the heating, so a single unreachable one is knowable by
    #: subtraction. Reported, because it is a weaker measurement than the others.
    by_difference: bool = False

    def as_dict(self) -> dict:
        return {"name": self.name, "quanta": self.quanta, "share": self.share,
                "error": self.error, "slope": self.slope,
                "halving_buys": self.halving_buys,
                "attributable": self.attributable,
                "by_difference": self.by_difference}


@dataclass
class BudgetReport:
    name: str
    model: str
    n_gate_pairs: int = 0
    #: the floor: what the gates cost even at n-bar = 0
    floor_error: float = 0.0
    total_error: float = 0.0
    channels: tuple[Channel, ...] = ()
    notes: list[str] = field(default_factory=list)

    @property
    def heating_error(self) -> float:
        """The part of the infidelity that heating is responsible for."""
        return self.total_error - self.floor_error

    def as_dict(self) -> dict:
        return {"name": self.name, "model": self.model,
                "n_gate_pairs": self.n_gate_pairs,
                "floor_error": self.floor_error, "total_error": self.total_error,
                "heating_error": self.heating_error,
                "channels": [c.as_dict() for c in self.channels],
                "notes": list(self.notes)}

    def table(self) -> str:
        w = max((len(c.name) for c in self.channels), default=8)
        out = [f"{'channel':<{w}}  {'quanta':>12}  {'share':>7}  {'error':>10}  "
               f"{'halving buys':>13}"]
        out.append("-" * len(out[0]))
        for c in sorted(self.channels,
                        key=lambda c: (c.attributable, c.error), reverse=True):
            if not c.attributable:
                out.append(f"{c.name:<{w}}  {c.quanta:>12,.1f}  {'--':>7}  "
                           f"{'not attributable':>10}  {'--':>13}")
            else:
                mark = " (by difference)" if c.by_difference else ""
                out.append(f"{c.name:<{w}}  {c.quanta:>12,.1f}  {100*c.share:>6.1f}%  "
                           f"{c.error:>10.4f}  {c.halving_buys:>13.4f}{mark}")
        return "\n".join(out)


class _Scaled:
    """A cost model with one heating channel multiplied by `k`, and nothing else touched.

    Delegation rather than a subclass, so this works for every model there is and cannot
    drift when one of them gains a method.
    """

    def __init__(self, inner: CostModel, channel: str, k: float):
        self._inner = inner
        self._channel = channel
        self._k = float(k)

    def __getattr__(self, item):
        return getattr(self._inner, item)

    def move(self, arch, seg, src, dst, *, entails=()):
        return self._rescale(self._inner.move(arch, seg, src, dst, entails=entails))

    def nonTransport(self, *a, **kw):                       # pragma: no cover - parity
        return self._rescale(self._inner.nonTransport(*a, **kw))

    def gate(self, *a, **kw):
        return self._rescale(self._inner.gate(*a, **kw))

    def anomalous_per_us(self, arch):
        # THE CHANNEL THE CHARGE-RESCALE CANNOT SEE. Anomalous heating is not returned as
        # a charge -- the replay accrues it from elapsed time at a rate the MODEL owns
        # (`anomalous_per_us`). Rescaling only the returned charges leaves it untouched
        # and reports 0.0 for a channel carrying thousands of quanta. Scaling it here
        # instead is the same intervention applied at the place this channel is actually
        # metered, so it gets a real two-point derivative like the others.
        k = self._k if self._channel == "anomalous" else 1.0
        return self._inner.anomalous_per_us(arch) * k

    def _rescale(self, charge):
        q = dict(getattr(charge, "quanta", None) or {})
        if self._channel in q:
            out = copy.copy(charge)
            q[self._channel] = q[self._channel] * self._k
            object.__setattr__(out, "quanta", q) if not hasattr(out, "__dict__") \
                else setattr(out, "quanta", q)
            return out
        return charge


def _replay(prog: TSIR, arch: Architecture, model) -> tuple[float, int, dict]:
    from ..verify import replay as _run

    r = _run(prog, arch, model, check_rules=False, keep_cycles=False)
    return (float(r.gate_error_sum), int(r.n_gate_pairs),
            {k: float(v) for k, v in r.quanta_components.items()})


def error_budget(prog: TSIR, arch: Architecture, model: CostModel) -> BudgetReport:
    """Decompose the gate infidelity by heating channel, with exact derivatives."""
    base_error, n_pairs, comps = _replay(prog, arch, model)

    spec = arch.primitives.scalar("ms_gate")
    eps0 = 1.0 - float(spec.get("fidelity_at_n0", 1.0))
    rep = BudgetReport(name=prog.name, model=getattr(model, "name", "?"),
                       n_gate_pairs=n_pairs, floor_error=n_pairs * eps0,
                       total_error=base_error)

    if n_pairs == 0:
        rep.notes.append("this programme runs no gates, so it has no infidelity to "
                         "attribute -- the heating is real but nothing reads it")
        rep.channels = tuple(Channel(c, comps.get(c, 0.0)) for c in CHANNELS)
        return rep

    chans: list[Channel] = []
    for name in CHANNELS:
        q = comps.get(name, 0.0)
        if q == 0.0:
            chans.append(Channel(name, 0.0))
            continue
        # TWO POINTS ON A STRAIGHT LINE. `E` is linear in this channel's contribution, so
        # doubling it and differencing gives the exact derivative -- no step size, no
        # truncation error.
        doubled, _, comps2 = _replay(prog, arch, _Scaled(model, name, 2.0))
        d = doubled - base_error
        c = Channel(name, quanta=q, error=d, slope=d, halving_buys=0.5 * d)
        # DID THE SCALING ACTUALLY REACH THIS CHANNEL? Each channel is metered somewhere
        # specific and `_Scaled` has to intervene at that place -- returned charges for
        # most, `anomalous_per_us` for the one the replay accrues from elapsed time. If a
        # future channel is metered somewhere neither reaches, the doubled replay will not
        # show 2x the quanta, and reporting the resulting 0.0 as its error would claim a
        # channel carrying thousands of quanta contributes nothing. Verify, don't assume.
        if abs(comps2.get(name, 0.0) - 2.0 * q) > 1e-6 * max(1.0, q):
            c.error = float("nan")
            c.slope = float("nan")
            c.halving_buys = float("nan")
            c.attributable = False
        chans.append(c)

    # FALLBACK: ATTRIBUTION BY DIFFERENCE. Every channel shipped today is measured
    # directly above, so this does not fire -- it is here for a future channel metered
    # somewhere `_Scaled` cannot reach. The channels partition the heating, so if exactly
    # ONE is unreachable, conservation gives it: whatever the others leave unaccounted for
    # is its contribution. That is a derivation, not a guess. With two or more the
    # residual is joint and stays unattributed rather than being split by guesswork.
    # It is flagged `by_difference` because it is a weaker measurement than a derivative.
    opaque_c = [c for c in chans if not c.attributable and c.quanta]
    if len(opaque_c) == 1:
        c = opaque_c[0]
        c.error = rep.total_error - rep.floor_error - sum(
            x.error for x in chans if x.attributable)
        c.slope = c.error
        c.halving_buys = 0.5 * c.error
        c.attributable = True
        c.by_difference = True

    attributable = sum(c.error for c in chans if c.attributable)
    for c in chans:
        if not c.attributable:
            # NOT ZERO. A share of zero would say this channel contributes nothing.
            c.share = float("nan")
        else:
            c.share = (c.error / attributable) if attributable else 0.0
    rep.channels = tuple(chans)

    if attributable > 0:
        worst = max((c for c in chans if c.attributable),
                    key=lambda c: c.error, default=None)
        if worst is not None:
            rep.notes.append(
                f"{worst.name} is {100*worst.share:.0f}% of the infidelity heating "
                f"causes; halving it buys {worst.halving_buys:.4f} in summed gate error")
        diffed = [c.name for c in chans if c.by_difference]
        if diffed:
            rep.notes.append(
                "no direct measurement reaches " + ", ".join(diffed) + ", so its figure "
                "is what the other channels leave unaccounted for -- conservation rather "
                "than a derivative, and a weaker number than the rest of this table")
        opaque = [c.name for c in chans if not c.attributable and c.quanta]
        if opaque:
            rep.notes.append(
                "two or more channels cannot be isolated, so their joint residual stays "
                "unattributed rather than being split by guesswork: " + ", ".join(opaque))
    floor_share = rep.floor_error / base_error if base_error else 0.0
    rep.notes.append(
        f"{100*floor_share:.0f}% of the total is the gate's own floor at n-bar 0 "
        f"({n_pairs} pairs x {eps0:.2e}), which no amount of transport work removes")
    return rep
