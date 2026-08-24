"""Assemble the trusted checker's input: the certificate, plus facts read from the device.

The compiler produces the certificate.  It does **not** get to say which traps can host a
gate, or which pairs of traps are one machine cycle apart -- those are read here, out of
the architecture document, by code the compiler does not run.  Otherwise a buggy (or
merely optimistic) compiler could widen the set of legal moves by asserting it, and the
checker would faithfully verify a schedule against the compiler's own idea of the machine.

The derivation direction matters for safety.  If this BFS and the compiler's trap graph
ever disagree, the checker sees moves that are not in its hop set and **rejects** -- the
failure is in the safe direction, which is why re-deriving here is worth the duplication.

    python Compiler/bridge/mk_qcheck_input.py build/out/ghz8_grid9x9 \
        --arch build/grid9x9.expanded.json -o build/qcheck_ghz8_grid9x9.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def trap_hops(arch: dict, max_transit: int = 3) -> list[dict]:
    """Every ordered pair of traps one machine cycle apart.

    An ion never rests on a junction -- R2 allows at most one on a degree->=3 node at any
    instant -- so a hop leaves a trap, crosses only non-trap nodes, and lands on a trap.
    """
    geom = arch["geometry"]
    kind = {n["id"]: n.get("kind", "site") for n in geom["nodes"]}
    adj: dict[str, list[tuple[str, str]]] = {}
    for s in geom["segments"]:
        a, b = s["ends"]
        adj.setdefault(a, []).append((b, s["id"]))
        adj.setdefault(b, []).append((a, s["id"]))

    out: list[dict] = []
    for src in [n for n, k in kind.items() if k == "site"]:
        found: dict[str, int] = {}

        def walk(node: str, used: list[str], depth: int) -> None:
            if depth > max_transit:
                return
            for nbr, seg in adj.get(node, []):
                if seg in used:
                    continue
                if kind.get(nbr) == "site":
                    if nbr != src and (nbr not in found or len(used) + 1 < found[nbr]):
                        found[nbr] = len(used) + 1
                else:
                    walk(nbr, used + [seg], depth + 1)

        walk(src, [], 0)
        for dst in found:
            out.append({"from": src, "to": dst})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("prefix", help="the compiler's -o prefix (reads <prefix>.qcert.json)")
    ap.add_argument("--arch", required=True, help="the EXPANDED architecture document")
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args(argv)

    cert = json.loads(Path(args.prefix + ".qcert.json").read_text(encoding="utf-8"))
    arch = json.loads(Path(args.arch).read_text(encoding="utf-8"))

    gate_sites = sorted(n for n, c in arch["node_caps"].items() if c.get("gate"))
    hops = trap_hops(arch)
    # The cyclic node order of each closed loop, so the checker can replay a rotation
    # witness.  Read out of the ARCHITECTURE, exactly as `hops` and `gate_sites` are: a
    # compiler that claimed its own loop order could rotate ions to wherever it liked.
    loops = {
        l["id"]: list(l["nodes"])
        for l in arch["geometry"].get("loops", [])
        if l.get("closed", l.get("kind") == "ring")
    }

    doc = {
        "circuit": cert["circuit"],
        "arch": cert["arch"],
        "n_qubits": cert["n_qubits"],
        "map": cert["map"],
        "init": cert["init"],
        "circuit_ops": cert["circuit_ops"],
        "moves": cert["moves"],
        "rotations": cert.get("rotations", []),
        "gates": cert["gates"],
        "unrealised": cert["unrealised"],
        # from the DEVICE, not from the compiler
        "gate_sites": gate_sites,
        "hops": hops,
        "loops": loops,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(doc) + "\n", encoding="utf-8")
    print(f"{cert['circuit']} on {cert['arch']}: {len(cert['gates'])} witnesses, "
          f"{len(cert['moves'])} moves, {len(cert.get('rotations', []))} rotations "
          f"| device: {len(gate_sites)} gate-capable traps, {len(hops)} hops, "
          f"{len(loops)} closed loops")
    print(f"  -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
