"""One shape for every analysis, so the tool can run one it has never heard of.

`reach_report` and `error_budget` are functions with different signatures returning
different dataclasses.  Fine for a script; useless to a design tool, which has to offer
the architect a list of analyses, show each one's knobs, run whichever they pick, and plot
whatever it returns -- without a branch per analysis.  qiskit-metal solves this with
`QAnalysis`: `default_setup` declares the knobs, `data_labels` declares the outputs, `run`
is the single entry point, and `run_sweep` varies one knob and collects a curve.  The same
shape fits here, and the sweep is the part an architect actually wants: *move the coolers
and watch the stranded count*, not one report at one setting.

Three things make this more than ceremony over a function call:

**Setup keys are validated, and an unknown one raises.**  qiskit-metal warns and carries
on.  Here a typo would mean reporting a number for a design nobody asked about -- the
knob silently keeps its default, the analysis succeeds, and the answer is confidently
about the wrong machine.  There is no version of that worth a warning, so `budgt=2.0`
raises and names the keys that do exist.

**The run arguments are kept.**  This project's discipline is that every number can be
traced to what produced it; a report that cannot say what setup it came from breaks that
at the last step.  `run_args` records exactly what was passed.

**A sweep survives a bad point.**  A parameter sweep walks into invalid designs by
construction -- that is what sweeping is -- and losing twenty good points to the
twenty-first is the difference between a usable instrument and one people stop running.
Failures are recorded per point, with the exception, and the curve keeps its shape.
"""

from __future__ import annotations

import copy
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

__all__ = ["QCCDAnalysis", "SweepResult", "SweepPoint"]


def _deep_merge(base: dict, over: Mapping) -> dict:
    """Merge `over` into a copy of `base`, one level into nested dicts."""
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, Mapping) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _check_keys(defaults: Mapping, given: Mapping, path: str = "") -> None:
    """Reject a key the analysis does not declare, rather than ignoring it.

    An ignored knob is worse than a crash: the run succeeds and reports a number about a
    design the architect did not ask for.
    """
    for k, v in given.items():
        if k not in defaults:
            where = f"{path}{k}"
            known = ", ".join(sorted(str(x) for x in defaults)) or "(none)"
            raise KeyError(
                f"{where!r} is not a setup key of this analysis; it would have been "
                f"silently ignored and the run would have reported a number for the "
                f"default instead. Known keys here: {known}")
        if isinstance(v, Mapping) and isinstance(defaults[k], Mapping):
            _check_keys(defaults[k], v, path=f"{path}{k}.")


@dataclass
class SweepPoint:
    """One setting of the swept knob, and what came back."""

    value: Any
    setup: dict = field(default_factory=dict)
    data: dict = field(default_factory=dict)
    ok: bool = True
    error: str | None = None

    def as_dict(self) -> dict:
        return {"value": self.value, "setup": self.setup, "data": self.data,
                "ok": self.ok, "error": self.error}


@dataclass
class SweepResult:
    """A curve: one knob varied, the analysis's outputs collected at each setting."""

    analysis: str
    key: str
    points: list[SweepPoint] = field(default_factory=list)
    data_labels: tuple[str, ...] = ()

    @property
    def failures(self) -> list[SweepPoint]:
        return [p for p in self.points if not p.ok]

    def series(self, label: str) -> list[tuple[Any, Any]]:
        """`(knob, value)` pairs for one output label, skipping the points that failed.

        Dropping failures silently would draw a curve through a gap as if it were
        continuous, so `failures` is public and `table()` prints the count.
        """
        return [(p.value, p.data.get(label)) for p in self.points if p.ok]

    def as_dict(self) -> dict:
        return {"analysis": self.analysis, "key": self.key,
                "data_labels": list(self.data_labels),
                "points": [p.as_dict() for p in self.points],
                "n_failed": len(self.failures)}

    def table(self) -> str:
        labels = [x for x in self.data_labels
                  if any(isinstance(p.data.get(x), (int, float)) for p in self.points)]
        w = max(len(self.key), 12)
        head = f"{self.key:<{w}}  " + "  ".join(f"{x:>14}" for x in labels)
        out = [head, "-" * len(head)]
        for p in self.points:
            if not p.ok:
                out.append(f"{str(p.value):<{w}}  " +
                           "  ".join(f"{'failed':>14}" for _ in labels))
                continue
            cells = []
            for x in labels:
                v = p.data.get(x)
                cells.append(f"{v:>14.4f}" if isinstance(v, float) else f"{v!s:>14}")
            out.append(f"{str(p.value):<{w}}  " + "  ".join(cells))
        if self.failures:
            out.append(f"({len(self.failures)} of {len(self.points)} settings failed; "
                       f"see .failures for why -- they are not plotted)")
        return "\n".join(out)


class QCCDAnalysis:
    """Base class: declare the knobs and the outputs, implement `_run`.

    Subclasses set `default_setup` and `data_labels` and implement `_run(self) -> dict`
    keyed by `data_labels`.  Everything else -- validation, merging, recording, sweeping
    -- is inherited.
    """

    #: the knobs, with their defaults. A key not in here is rejected, not ignored.
    default_setup: dict = {}
    #: what `_run` returns, by name. The tool plots these without knowing what they mean.
    data_labels: tuple[str, ...] = ()
    #: one line, for the analysis picker
    summary: str = ""

    def __init__(self, **setup):
        _check_keys(self.default_setup, setup)
        self._setup = _deep_merge(self.default_setup, setup)
        self._data: dict = {}
        self._run_args: dict = {}
        self._ran = False

    # -- setup ---------------------------------------------------------------------
    @property
    def setup(self) -> dict:
        return copy.deepcopy(self._setup)

    def setup_update(self, **kw) -> None:
        _check_keys(self.default_setup, kw)
        self._setup = _deep_merge(self._setup, kw)

    # -- running -------------------------------------------------------------------
    def run(self, **kw) -> dict:
        """Apply any overrides, run, record what it took, return the data."""
        if kw:
            self.setup_update(**kw)
        self._run_args = copy.deepcopy(self._setup)
        data = self._run()
        missing = [x for x in self.data_labels if x not in data]
        if missing:
            # A label the tool will plot and find absent. Better here than as a blank
            # axis three steps later.
            raise KeyError(f"{type(self).__name__}._run did not return declared "
                           f"data_labels: {', '.join(missing)}")
        self._data = data
        self._ran = True
        return dict(data)

    def _run(self) -> dict:                                  # pragma: no cover - abstract
        raise NotImplementedError

    @property
    def run_args(self) -> dict:
        """Exactly the setup the last `run` used -- what the number can be traced to."""
        return copy.deepcopy(self._run_args)

    def get_data(self, label: str | None = None):
        if not self._ran:
            raise RuntimeError(f"{type(self).__name__} has not been run")
        if label is None:
            return dict(self._data)
        if label not in self.data_labels:
            raise KeyError(f"{label!r} is not one of this analysis's data_labels: "
                           f"{', '.join(self.data_labels)}")
        return self._data.get(label)

    # -- sweeping ------------------------------------------------------------------
    def sweep(self, key: str, values: Iterable, *,
              on: Callable[["QCCDAnalysis", Any], None] | None = None) -> SweepResult:
        """Vary one knob and collect the outputs at each setting.

        `key` may be dotted for a nested knob (`model.junction_scale`).  A setting that
        raises is recorded and the sweep continues: a parameter sweep walks into invalid
        designs by construction, and losing the whole curve to one of them is how an
        instrument stops being used.
        """
        parts = key.split(".")
        probe: Any = self.default_setup
        for p in parts:
            if not isinstance(probe, Mapping) or p not in probe:
                raise KeyError(f"{key!r} is not a setup key of "
                               f"{type(self).__name__}; known: "
                               f"{', '.join(sorted(map(str, self.default_setup)))}")
            probe = probe[p]

        res = SweepResult(analysis=type(self).__name__, key=key,
                          data_labels=tuple(self.data_labels))
        saved = self.setup
        for v in values:
            nested: Any = v
            for p in reversed(parts):
                nested = {p: nested}
            point = SweepPoint(value=v)
            try:
                self._setup = _deep_merge(saved, nested)
                if on is not None:
                    on(self, v)
                point.data = self.run()
                point.setup = self.run_args
            except Exception as exc:                          # noqa: BLE001
                point.ok = False
                point.error = f"{type(exc).__name__}: {exc}"
                point.setup = self.setup
                if _VERBOSE:
                    traceback.print_exc()
            res.points.append(point)
        self._setup = saved
        return res

    # -- description ---------------------------------------------------------------
    @classmethod
    def describe(cls) -> dict:
        """What the picker needs to offer this analysis without knowing what it is."""
        return {"name": cls.__name__, "summary": cls.summary or (cls.__doc__ or "").strip(),
                "setup": copy.deepcopy(cls.default_setup),
                "data_labels": list(cls.data_labels)}


#: set true to see tracebacks from failing sweep points
_VERBOSE = False
