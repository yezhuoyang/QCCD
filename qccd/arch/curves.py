"""Primitive `(duration, quanta)` curves.  PLAN §3.2.

A primitive is not a scalar cost.  Slowing a transport buys motional quanta, so every
transport primitive is a *curve* of operating points and the compiler picks one per
instance (PLAN §7 pass 5).

The corpus supplies two mutually incompatible primitive tables, differing by 2-3x in
time (`Knowledge: q_heating_rate_measurement`).  Rather than pick one, each curve point
is tagged with the `table` it came from, and the whole stack can be re-run against
either table so that *ranking stability under parameter uncertainty* is itself
measurable (PLAN §3.2, milestone M5).

Tables in use
-------------
``qccdsim_jones``
    arXiv:2510.23519 §"reconfiguration" primitive table, as reproduced in PLAN §0.3.
    shuttle 5 us / 0.1 quanta, split-merge 80 us / 6 quanta, junction 100 us / 3 quanta.
``transport_excitation``
    arXiv:2605.25118 measured transport-excitation framework: merge/split 30 us at
    n~1 versus 40 us at n~0.1; swap 18/20 us; single-ion transport 12/14 us.
``cyclone``
    arXiv:2511.15910 machine model: split/merge 80 us, move 10 us, junction crossing
    10/100/120 us by degree 2/3/4.  Carries no heating numbers -- Cyclone assumes
    transport contributes no error (see `Knowledge: q_transport_error_model`), so its
    points inherit the Jones quanta where a value is needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Iterator, Mapping, Sequence

__all__ = [
    "CurvePoint",
    "Curve",
    "DegreeCurve",
    "PrimitiveSet",
    "OperatingPointPolicy",
    "TABLES",
]

#: Every primitive-table name the loader will accept.  A curve point tagged with an
#: unknown table is a validation error -- silent typos in a table name would make a
#: whole sweep compare a table against itself.
TABLES = (
    "qccdsim_jones",
    "transport_excitation",
    "cyclone",
    "measured",
    "deck_v3",
    "local",
)


@dataclass(frozen=True, slots=True)
class CurvePoint:
    """One achievable `(duration, quanta)` operating point of a primitive."""

    us: float
    quanta: float
    table: str = "qccdsim_jones"
    source: str | None = None
    label: str | None = None

    def to_json(self) -> dict:
        d: dict = {"us": self.us, "quanta": self.quanta, "table": self.table}
        if self.source is not None:
            d["source"] = self.source
        if self.label is not None:
            d["label"] = self.label
        return d

    @staticmethod
    def from_json(d: Mapping) -> "CurvePoint":
        return CurvePoint(
            us=float(d["us"]),
            quanta=float(d["quanta"]),
            table=str(d.get("table", "qccdsim_jones")),
            source=d.get("source"),
            label=d.get("label"),
        )


@dataclass(frozen=True, slots=True)
class Curve:
    """A set of operating points for one primitive.

    Points are *not* required to be Pareto-optimal against each other: two different
    tables describe two different devices, and a point that is dominated within one
    table may be the only measured point of another.
    """

    points: tuple[CurvePoint, ...]

    def __iter__(self) -> Iterator[CurvePoint]:
        return iter(self.points)

    def __len__(self) -> int:
        return len(self.points)

    def tables(self) -> tuple[str, ...]:
        seen: list[str] = []
        for p in self.points:
            if p.table not in seen:
                seen.append(p.table)
        return tuple(seen)

    def by_table(self, table: str) -> "Curve":
        pts = tuple(p for p in self.points if p.table == table)
        return Curve(pts)

    def pick(self, policy: "OperatingPointPolicy") -> CurvePoint:
        """Choose one operating point.  Raises if the policy selects nothing."""
        return policy.choose(self)

    def to_json(self) -> list[dict]:
        return [p.to_json() for p in self.points]

    @staticmethod
    def from_json(v: Sequence[Mapping]) -> "Curve":
        return Curve(tuple(CurvePoint.from_json(p) for p in v))


@dataclass(frozen=True, slots=True)
class DegreeCurve:
    """A curve keyed by node degree -- the junction-crossing primitive.

    Rule R18: a node is a *junction* only where three or more trap axes meet.  Degree-2
    bends are ordinary transport with a continuous RF null, so they carry no junction
    cost at all; the corrected model charges them one shuttle (PLAN §0.5).  A degree-2
    entry may still be present because Cyclone's table has one -- the cost model decides
    whether to honour it, and the corrected model does not.
    """

    by_degree: Mapping[int, Curve]

    def get(self, degree: int) -> Curve | None:
        return self.by_degree.get(degree)

    def degrees(self) -> tuple[int, ...]:
        return tuple(sorted(self.by_degree))

    def to_json(self) -> dict:
        return {str(k): v.to_json() for k, v in sorted(self.by_degree.items())}

    @staticmethod
    def from_json(d: Mapping) -> "DegreeCurve":
        return DegreeCurve({int(k): Curve.from_json(v) for k, v in d.items()})


@dataclass(frozen=True, slots=True)
class OperatingPointPolicy:
    """How the compiler resolves a curve to a single operating point.

    ``table``    restrict to points measured on one device/table before choosing.
    ``objective`` ``"fastest"`` (min us), ``"coolest"`` (min quanta), or
                 ``"balanced"`` (min ``us/us_ref + quanta/quanta_ref`` -- lexicographic
                 fallback to fastest on ties).
    """

    table: str | None = "qccdsim_jones"
    objective: str = "fastest"

    def choose(self, curve: Curve) -> CurvePoint:
        pts: Sequence[CurvePoint] = curve.points
        if self.table is not None:
            restricted = [p for p in pts if p.table == self.table]
            if restricted:
                pts = restricted
        if not pts:
            raise ValueError("operating-point policy selected no curve point")
        if self.objective == "fastest":
            return min(pts, key=lambda p: (p.us, p.quanta))
        if self.objective == "coolest":
            return min(pts, key=lambda p: (p.quanta, p.us))
        if self.objective == "balanced":
            us_ref = min(p.us for p in pts) or 1.0
            q_ref = min((p.quanta for p in pts if p.quanta > 0), default=1.0) or 1.0
            return min(pts, key=lambda p: (p.us / us_ref + p.quanta / q_ref, p.us))
        raise ValueError(f"unknown operating-point objective {self.objective!r}")


@dataclass(frozen=True, slots=True)
class PrimitiveSet:
    """The `primitives` block of an `.arch.json`, parsed.

    ``curves``        primitive name -> Curve            (transport primitives)
    ``degree_curves`` primitive name -> DegreeCurve      (junction_cross)
    ``scalars``       primitive name -> raw dict         (gates, measure, reset, cool)
    """

    curves: Mapping[str, Curve] = field(default_factory=dict)
    degree_curves: Mapping[str, DegreeCurve] = field(default_factory=dict)
    scalars: Mapping[str, Mapping] = field(default_factory=dict)

    def curve(self, name: str) -> Curve:
        try:
            return self.curves[name]
        except KeyError:
            raise KeyError(f"architecture declares no `{name}` curve") from None

    def degree_curve(self, name: str) -> DegreeCurve:
        try:
            return self.degree_curves[name]
        except KeyError:
            raise KeyError(f"architecture declares no `{name}` degree curve") from None

    def scalar(self, name: str) -> Mapping:
        try:
            return self.scalars[name]
        except KeyError:
            raise KeyError(f"architecture declares no `{name}` primitive") from None

    def tables(self) -> tuple[str, ...]:
        seen: list[str] = []
        for c in self.curves.values():
            for t in c.tables():
                if t not in seen:
                    seen.append(t)
        for dc in self.degree_curves.values():
            for c in dc.by_degree.values():
                for t in c.tables():
                    if t not in seen:
                        seen.append(t)
        return tuple(seen)

    def table_coverage(self, table: str) -> dict[str, bool]:
        """primitive name -> does `table` supply at least one point for it?

        `OperatingPointPolicy` falls back to the union of tables when the requested one
        is silent about a primitive, so that a partial table is usable at all.  That
        fallback would quietly turn a two-table ranking-stability comparison into a
        comparison of a table with itself, so it is reported rather than hidden.
        """
        out: dict[str, bool] = {}
        for name, curve in self.curves.items():
            out[name] = any(p.table == table for p in curve.points)
        for name, dcurve in self.degree_curves.items():
            out[name] = any(
                p.table == table for c in dcurve.by_degree.values() for p in c.points
            )
        return out

    def to_json(self) -> dict:
        out: dict = {}
        for name, curve in self.curves.items():
            out[name] = {"curve": curve.to_json()}
        for name, dcurve in self.degree_curves.items():
            out.setdefault(name, {})["curve_by_degree"] = dcurve.to_json()
        for name, raw in self.scalars.items():
            out.setdefault(name, {}).update(dict(raw))
        return out

    @staticmethod
    def from_json(d: Mapping) -> "PrimitiveSet":
        curves: dict[str, Curve] = {}
        degree_curves: dict[str, DegreeCurve] = {}
        scalars: dict[str, dict] = {}
        for name, spec in d.items():
            rest = {k: v for k, v in spec.items() if k not in ("curve", "curve_by_degree")}
            if "curve" in spec:
                curves[name] = Curve.from_json(spec["curve"])
            if "curve_by_degree" in spec:
                degree_curves[name] = DegreeCurve.from_json(spec["curve_by_degree"])
            if rest:
                scalars[name] = rest
        return PrimitiveSet(curves=curves, degree_curves=degree_curves, scalars=scalars)


def merge_curves(*curves: Iterable[CurvePoint]) -> Curve:
    """Concatenate curve points, preserving order and dropping exact duplicates."""
    out: list[CurvePoint] = []
    for group in curves:
        for p in group:
            if p not in out:
                out.append(p)
    return Curve(tuple(out))
