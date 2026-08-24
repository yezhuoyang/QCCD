"""Export an architecture in *expanded* form, for the OCaml compiler to consume.

The OCaml side must never reimplement `qccd/arch/generators.py`.  A generator is a
parameter tuple that *expands* into an explicit graph, and the expansion is where every
derived quantity the cost model hangs off is computed -- node degree (R18: degree >= 3 is
a junction), loop corners, resolved capacities.  Recomputing any of that in a second
language is how two implementations silently disagree about what a junction is.

So the contract is: Python owns expansion, OCaml consumes the expanded document.
`Architecture.to_json(expanded=True)` already emits it; this script adds the two things
the compiler needs that are not in the document -- the SIMD class table (which is
generated from the control model, not stored) and a per-node capability summary -- and
pins a hash so a certificate can bind itself to one architecture.

    python Compiler/bridge/export_arch.py arch/ring144_24v.arch.json \
        -o Compiler/build/ring144_24v.expanded.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qccd.arch import Architecture  # noqa: E402


def expand(arch: Architecture) -> dict:
    """The expanded architecture, plus what the compiler needs and the document omits."""
    doc = arch.to_json(expanded=True)
    dev = arch.device

    # Capabilities are stored per zone *type*; the router needs them per node, and
    # asking "can this node host a 2Q gate" is the single most common query it makes.
    doc["node_caps"] = {
        n.id: {
            "capacity": n.capacity,
            "zone_type": n.zone_type,
            "degree": dev.degree(n.id),
            "is_junction": dev.is_junction(n.id),
            "gate": bool(arch.can(n.id, "gate")),
            "spam": bool(arch.can(n.id, "spam")),
            "cool": bool(arch.can(n.id, "cool")),
            "labels": list(n.labels),
        }
        for n in dev.nodes.values()
    }

    # The SIMD class table is generated from the control model rather than stored, and
    # it is the R4 constraint the router is encoded against -- so it has to travel.
    doc["simd_classes"] = {k: dict(v) for k, v in arch.simd_classes.items()}
    doc["max_simd_classes_per_cycle"] = arch.max_simd_classes()
    doc["entails"] = {k: list(arch.entails(k)) for k in arch.simd_classes}

    # Corners are a property of a loop, not of a node, so they cannot be read off
    # `node_caps`; the transport cost model needs them per loop.
    doc["loop_corners"] = {lid: sorted(dev.corners(lid)) for lid in dev.loops}
    doc["corner_endpoints"] = dict(dev.corner_endpoints)
    return doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("arch", help="path to a .arch.json")
    ap.add_argument("-o", "--out", required=True, help="where to write the expanded form")
    args = ap.parse_args(argv)

    arch = Architecture.from_json(json.loads(Path(args.arch).read_text(encoding="utf-8")))
    doc = expand(arch)

    # The hash covers the expanded content, so a certificate that names it is bound to
    # the graph the compiler actually saw -- not to a generator parameter tuple that
    # could expand differently under a different version.
    body = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    doc["sha256"] = hashlib.sha256(body.encode("utf-8")).hexdigest()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")

    dev = arch.device
    print(f"{arch.name}: {len(dev.nodes)} nodes, {len(dev.segments)} segments, "
          f"{len(dev.junction_nodes)} junctions, {len(doc['simd_classes'])} SIMD classes "
          f"(max {doc['max_simd_classes_per_cycle']}/cycle)")
    print(f"  -> {out}  sha256 {doc['sha256'][:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
