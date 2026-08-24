"""The C1 oracle: parse QASM with qiskit and emit the compiler's canonical circuit JSON.

C1's claim is that the OCaml front end builds the same circuit and the same dependency
DAG as `qiskit.converters.circuit_to_dag`.  A claim like that is only worth making if
both sides are pinned to the *same* reading of the source, so two details matter:

**`LEGACY_CUSTOM_INSTRUCTIONS`.**  Without it qiskit lowers some qelib1 gates to its own
preferred spellings (`u1` becomes a phase gate, and so on) and the comparison would fail
on a naming convention rather than on a parse.  With it, measured on qiskit 2.3.1, every
qelib1 name survives verbatim -- `u1`, `u2`, `u3`, `cu1`, `crz`, `rzz` -- which is what
lets the two sides be compared on names at all.

**The edges come from qiskit's DAG, not from a re-derivation.**  It would be easy, and
worthless, to rebuild the per-wire orderings here from `circuit.data`: that would test the
OCaml code against a Python transcription of the same rule I wrote in OCaml.  Instead the
edges are read out of the real `DAGCircuit`, so the test compares against the object the
platform's users would actually get.

That requires mapping a `DAGOpNode` back to its position in program order.  The mapping
is by node id, and it is *checked* rather than assumed: `_align` verifies that the zipped
pairs agree on name, qubits and clbits, and raises if they do not.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from qiskit import qasm2
from qiskit.converters import circuit_to_dag


def _num(x: float):
    """Match the OCaml writer: integral values as ints, else 12 significant figures."""
    f = float(x)
    if f.is_integer() and abs(f) < 1e15:
        return int(f)
    return float(f"{f:.12g}")


def _align(circuit, dag):
    """Map each DAGOpNode to its index in program order, and verify the mapping.

    `circuit_to_dag` appends op nodes in `circuit.data` order, so sorting op nodes by
    node id recovers that order.  The assumption is cheap to check and expensive to get
    wrong, so it is checked.
    """
    op_nodes = sorted(dag.op_nodes(), key=lambda n: n._node_id)
    if len(op_nodes) != len(circuit.data):
        raise SystemExit(
            f"oracle: {len(op_nodes)} DAG op nodes but {len(circuit.data)} instructions")
    index_of = {}
    for i, (node, inst) in enumerate(zip(op_nodes, circuit.data)):
        if node.op.name != inst.operation.name:
            raise SystemExit(
                f"oracle: node {i} is {node.op.name!r} but instruction {i} is "
                f"{inst.operation.name!r}; the id-order assumption is wrong")
        if [circuit.find_bit(q).index for q in node.qargs] != [
            circuit.find_bit(q).index for q in inst.qubits
        ]:
            raise SystemExit(f"oracle: node {i} qubits disagree with instruction {i}")
        index_of[node._node_id] = i
    return index_of


def to_canonical(circuit, name: str) -> dict:
    dag = circuit_to_dag(circuit)
    index_of = _align(circuit, dag)

    qoff, qregs = 0, []
    for r in circuit.qregs:
        qregs.append({"name": r.name, "offset": qoff, "width": r.size})
        qoff += r.size
    coff, cregs = 0, []
    for r in circuit.cregs:
        cregs.append({"name": r.name, "offset": coff, "width": r.size})
        coff += r.size

    ops = []
    for i, inst in enumerate(circuit.data):
        entry = {
            "i": i,
            "name": inst.operation.name,
            "qubits": [circuit.find_bit(q).index for q in inst.qubits],
            "clbits": [circuit.find_bit(b).index for b in inst.clbits],
            "params": [_num(p) for p in inst.operation.params],
        }
        cond = getattr(inst.operation, "condition", None)
        if cond is not None:
            reg, val = cond
            entry["cond"] = [getattr(reg, "name", str(reg)), int(val)]
        ops.append(entry)

    # Edges straight off the DAGCircuit: keep only op->op, drop the input/output wire
    # nodes, which are an artefact of qiskit's representation rather than a dependency.
    edges = set()
    for src, dst, _wire in dag.edges():
        a, b = index_of.get(getattr(src, "_node_id", None)), index_of.get(
            getattr(dst, "_node_id", None))
        if a is not None and b is not None:
            edges.add((a, b))

    # Per-wire sequences, also from the DAG, by walking each wire's node chain.
    n_q = circuit.num_qubits
    wires = []
    for w in list(circuit.qubits) + list(circuit.clbits):
        idx = circuit.find_bit(w).index
        idx = idx if w in circuit.qubits else n_q + idx
        seq = [
            index_of[n._node_id]
            for n in dag.nodes_on_wire(w, only_ops=True)
        ]
        wires.append({"wire": idx, "ops": seq})
    wires.sort(key=lambda d: d["wire"])

    return {
        "name": name,
        "n_qubits": circuit.num_qubits,
        "n_clbits": circuit.num_clbits,
        "qregs": qregs,
        "cregs": cregs,
        "ops": ops,
        "wires": wires,
        "edges": sorted([list(e) for e in edges]),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("qasm")
    ap.add_argument("-o", "--out", default=None)
    args = ap.parse_args(argv)

    circuit = qasm2.load(args.qasm,
                         custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)
    name = Path(args.qasm).stem
    doc = to_canonical(circuit, name)

    text = json.dumps(doc, separators=(",", ":")) + "\n"
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"{name}: {doc['n_qubits']} qubits, {len(doc['ops'])} ops, "
              f"{len(doc['edges'])} edges -> {args.out}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
