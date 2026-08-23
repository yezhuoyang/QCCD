"""Layer 1 -- the architecture description language.  PLAN §3."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from .curves import (
    TABLES,
    Curve,
    CurvePoint,
    DegreeCurve,
    OperatingPointPolicy,
    PrimitiveSet,
)
from .builder import DeviceBuilder
from .device import Architecture, Device, ExpansionError, Loop, Node, Segment
from .generators import (
    GENERATORS,
    chain,
    dual_loop,
    expand_generator,
    grid,
    ladder,
    racetrack,
    ring,
)
from .schema import (
    ARCH_SCHEMA,
    SCHEMA_VERSION,
    ValidationError,
    check,
    validate_document,
)

__all__ = [
    "ARCH_SCHEMA",
    "SCHEMA_VERSION",
    "TABLES",
    "Architecture",
    "Curve",
    "CurvePoint",
    "DegreeCurve",
    "Device",
    "DeviceBuilder",
    "ExpansionError",
    "Loop",
    "Node",
    "OperatingPointPolicy",
    "PrimitiveSet",
    "Segment",
    "ValidationError",
    "GENERATORS",
    "chain",
    "check",
    "dual_loop",
    "ladder",
    "racetrack",
    "expand_generator",
    "grid",
    "load",
    "loads",
    "ring",
    "save",
    "validate_document",
]


def loads(text: str, *, validate: bool = True) -> Architecture:
    """Parse and expand an `.arch.json` document from text."""
    return Architecture.from_json(json.loads(text), validate=validate)


def load(path: str | Path, *, validate: bool = True) -> Architecture:
    """Parse and expand an `.arch.json` file."""
    return Architecture.from_json(
        json.loads(Path(path).read_text(encoding="utf-8")), validate=validate
    )


def save(
    arch: Architecture, path: str | Path, *, expanded: bool = False, indent: int = 2
) -> Path:
    """Write an architecture.  `expanded=False` keeps the compact generator form."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(arch.to_json(expanded=expanded), indent=indent) + "\n",
        encoding="utf-8",
    )
    return p


def round_trip(arch: Architecture, *, expanded: bool = True) -> Architecture:
    """Serialize and re-parse -- the M0 acceptance check, as one call."""
    return Architecture.from_json(arch.to_json(expanded=expanded))


def devices_equal(a: Device, b: Device) -> list[str]:
    """Structural diff of two expanded graphs.  Empty list means identical."""
    diffs: list[str] = []
    if set(a.nodes) != set(b.nodes):
        only_a = sorted(set(a.nodes) - set(b.nodes))[:5]
        only_b = sorted(set(b.nodes) - set(a.nodes))[:5]
        diffs.append(f"node ids differ (only in a: {only_a}, only in b: {only_b})")
    else:
        for nid, node in a.nodes.items():
            if node != b.nodes[nid]:
                diffs.append(f"node {nid}: {node} != {b.nodes[nid]}")
    if set(a.segments) != set(b.segments):
        diffs.append("segment ids differ")
    else:
        for sid, seg in a.segments.items():
            if seg != b.segments[sid]:
                diffs.append(f"segment {sid}: {seg} != {b.segments[sid]}")
    if set(a.loops) != set(b.loops):
        diffs.append("loop ids differ")
    else:
        for lid, loop in a.loops.items():
            if loop != b.loops[lid]:
                diffs.append(f"loop {lid} differs")
    return diffs[:20]


def summarize(arch: Architecture) -> Mapping:
    s = dict(arch.device.summary())
    s["name"] = arch.name
    s["primitive_tables"] = list(arch.primitives.tables())
    return s
