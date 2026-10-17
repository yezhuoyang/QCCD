"""Read-only direction audit for the Jones d=3 WISE investigation.

Run from the repository root: python3 -m qccd.repro.wise_audit
No schedule or alternative mapping is produced.
"""

from __future__ import annotations

import gzip
import itertools
import json
from pathlib import Path

from .importers import jones_device
from .sched_jones import stim_program


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    circuit = root / "Reproduce/jones2025/circuits/rotated_memory_z_d3_r1.stim"
    artifact = root / "Reproduce/jones2025/artifact/grid_rot_d3_c2.schedule.json.gz"
    with gzip.open(artifact, "rt") as f:
        raw = json.load(f)
    dev = jones_device(raw["device"])
    _, prog, _ = stim_program(circuit)
    ancillas = {q for q, p in prog.items()
                if ("op", "reset") in p[p.index(("op", "measure")):]}
    home = {str(i): f"T{t}" for t, ions in raw["initial_layout"].items() for i in ions}
    traps = {q: home[str(i)] for q, i in raw["qubit_to_ion"].items()}
    ends = {t: {next(n for n in ab if n != t): s
                for s, ab in dev.segments.items() if t in ab} for t in dev.sites}
    steps = [line.split()[1:] for line in circuit.read_text().splitlines()
             if line.startswith("CX ")]
    edges = []
    classes = []
    print("| Step | Ancilla → data | Physical route | Direction (coordinate signs) |")
    print("|---|---|---|---|")
    for k, qs in enumerate(steps):
        directions = set()
        for c, t in zip(qs[::2], qs[1::2]):
            a, d = (c, t) if c in ancillas else (t, c)
            edges.append((a, d, k))
            src, dst = traps[a], traps[d]
            j, = set(ends[src]) & set(ends[dst])
            points = [dev.sites[src]["pos"], dev.nodes[j]["pos"], dev.sites[dst]["pos"]]
            legs = tuple((v[0] - u[0], v[1] - u[1]) for u, v in zip(points, points[1:]))
            directions.add(legs)
            route = f"{src} —{ends[src][j]}→ {j} —{ends[dst][j]}→ {dst}"
            print(f"| {k + 1} | {a} → {d} | {route} | {legs} |")
        classes.append(len(directions))
    print()
    for k, count in enumerate(classes, 1):
        print(f"Step {k}: {count} direction classes.")

    # A common two-leg route through one junction has displacement (±60, ±60).
    # Enumerate a superset of device embeddings: integer coordinates in an infinite
    # grid, ignoring finite boundaries and resource conflicts. Failure here rules
    # out any embedding with distinct home traps on the actual device as well.
    consistent = injective = 0
    for vectors in itertools.product(tuple(itertools.product((-1, 1), repeat=2)), repeat=4):
        positions = {edges[0][0]: (0, 0)}
        ok = True
        while True:
            changed = False
            for a, d, k in edges:
                v = vectors[k]
                if a in positions and d in positions:
                    if tuple(positions[d][j] - positions[a][j] for j in (0, 1)) != v:
                        ok = False
                        break
                elif a in positions:
                    positions[d] = tuple(positions[a][j] + v[j] for j in (0, 1))
                    changed = True
                elif d in positions:
                    positions[a] = tuple(positions[d][j] - v[j] for j in (0, 1))
                    changed = True
            if not ok or not changed:
                break
        assert not ok or len(positions) == len(traps), "interaction graph must be connected"
        if ok:
            consistent += 1
            injective += len(set(positions.values())) == len(positions)
    print(f"Uniform one-junction vector assignments: 256 examined, {consistent} consistent, "
          f"{injective} with distinct home traps.")


if __name__ == "__main__":
    main()
