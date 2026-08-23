"""The two analyses, wearing the contract -- and therefore sweepable.

`reach_report` and `error_budget` already answer their questions.  What they could not do
is answer a *series* of them, which is the question an architect actually has: not "how
many traps are stranded" but "how many are stranded as I move the coolers apart", and not
"what is the infidelity" but "how much of it goes away as junction heating improves".

Both knobs below are chosen so the sweep means something:

* `ReachAnalysis.budget` overrides R7's `ms_gate.max_quanta`.  Sweeping it asks *how much
  better would gates have to get before no position on this device is unusable* -- a
  question about the machine that its own datasheet number cannot pose.
* `BudgetAnalysis.scale` multiplies one heating channel.  Sweeping `scale.junction` from 1
  down to 0 draws the curve the whole project points at: junction transits are 76% of the
  infidelity, so what does halving them actually buy, and where does the return stop.

The scales compose by nesting `_Scaled`, which is why it delegates by `__getattr__`
instead of subclassing -- a stack of them is still a cost model.
"""

from __future__ import annotations

import math
from pathlib import Path

from ..arch import Architecture, load
from ..cost import corrected_model, deck_model
from ..cost.models import QUANTA_COMPONENTS
from .base import QCCDAnalysis
from .budget import _Scaled, error_budget
from .reach import reach_report

__all__ = ["ReachAnalysis", "BudgetAnalysis", "ANALYSES", "get_analysis"]

_MODELS = {"corrected": corrected_model, "deck": deck_model}


def _resolve_arch(device) -> Architecture:
    if isinstance(device, Architecture):
        return device
    if device is None:
        raise ValueError("no device: set setup['device'] to an Architecture or a path")
    return load(device if isinstance(device, Path) else str(device))


def _resolve_model(name):
    if name in _MODELS:
        return _MODELS[name]()
    if hasattr(name, "move"):
        return name
    raise ValueError(f"unknown cost model {name!r}; known: {', '.join(_MODELS)}")


class ReachAnalysis(QCCDAnalysis):
    """Distances over the device, and which positions are structurally unusable."""

    summary = ("what the machine can do before anyone writes a programme: distances in "
               "the cost model's own units, and the traps that cannot reach a cooler "
               "and return inside R7's gate budget")

    default_setup = {
        "device": None,
        "model": "corrected",
        "metric": "quanta",
        #: override R7's ms_gate.max_quanta; None uses the device's own
        "budget": None,
    }
    data_labels = ("n_stranded", "stranded_fraction", "diameter", "unreachable_pairs",
                   "n_sites", "budget", "report")

    def _run(self) -> dict:
        s = self._setup
        arch = _resolve_arch(s["device"])
        rep = reach_report(arch, _resolve_model(s["model"]),
                           metric=s["metric"], budget=s["budget"])
        return {
            "n_stranded": len(rep.stranded),
            "stranded_fraction": (len(rep.stranded) / rep.n_sites) if rep.n_sites else 0.0,
            "diameter": rep.diameter,
            "unreachable_pairs": rep.unreachable_pairs,
            "n_sites": rep.n_sites,
            "budget": rep.budget,
            "report": rep,
        }


class BudgetAnalysis(QCCDAnalysis):
    """Where the infidelity goes, and what improving one channel buys."""

    summary = ("the gate infidelity split by heating channel, each with an exact "
               "derivative: what halving a channel buys in summed gate error")

    default_setup = {
        "device": None,
        "program": "deck",
        "model": "corrected",
        #: multiply one heating channel -- the what-if knob. 1.0 is the device as built.
        "scale": {c: 1.0 for c in QUANTA_COMPONENTS},
    }
    data_labels = ("total_error", "heating_error", "floor_error", "dominant",
                   "dominant_share", "shares", "report")

    def _run(self) -> dict:
        from ..compile.programs import build

        s = self._setup
        arch = _resolve_arch(s["device"])
        model = _resolve_model(s["model"])
        # COMPOSE BY NESTING. `_Scaled` delegates by `__getattr__`, so a stack of them is
        # still a cost model and each layer scales exactly its own channel.
        for chan, k in sorted(s["scale"].items()):
            if float(k) != 1.0:
                model = _Scaled(model, chan, float(k))
        rep = error_budget(build(arch, s["program"]), arch, model)

        usable = [c for c in rep.channels if c.attributable and c.quanta]
        dom = max(usable, key=lambda c: c.error, default=None)
        return {
            "total_error": rep.total_error,
            "heating_error": rep.heating_error,
            "floor_error": rep.floor_error,
            "dominant": dom.name if dom else None,
            "dominant_share": dom.share if dom else float("nan"),
            "shares": {c.name: c.share for c in rep.channels},
            "report": rep,
        }


#: every analysis the tool can offer, by the name an architect would pick
ANALYSES = {"reach": ReachAnalysis, "budget": BudgetAnalysis}


def get_analysis(name: str) -> type[QCCDAnalysis]:
    if name not in ANALYSES:
        raise KeyError(f"unknown analysis {name!r}; known: {', '.join(sorted(ANALYSES))}")
    return ANALYSES[name]
