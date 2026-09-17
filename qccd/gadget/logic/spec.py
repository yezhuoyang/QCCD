"""A gadget op's specification, as stabilizer flows, and the check that its circuit meets it.

A flow `P -> Q` says: the value P had on the ions before the op equals the value Q has on
them after, XOR some measurement records (the Pauli frame).  Three kinds:

    exact    no records, the sign as stated: the op carries P to Q untouched
    frame    some records (possibly none): P reaches Q up to a known Pauli correction
    measure  Q is the identity and at least one record: the op measures P

**Completeness.**  Flows that hold are only half a specification: a circuit that also
measured something extra would satisfy them too.  So every spec lists enough independent
flows to pin the op down: for an op that keeps `a` input qubits' information and delivers
`b` output qubits, the Choi state of any channel it could be is stabilized by at most
`a + b` independent Paulis, so `a + b` independent flows that hold leave only one channel
-- the intended one, up to the Pauli frame the records name.  `verify` checks the rank, so
a thin spec cannot pass as a complete one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import gf2
from .circuit import Circuit
from .tableau import NotClifford, pauli_bits, simulate

__all__ = ["Flow", "Spec", "verify_flows"]


@dataclass
class Flow:
    name: str
    inp: dict[str, str]
    out: dict[str, str]
    kind: str = "exact"                    # exact | frame | measure
    sign: int = 0
    #: what the flow is about: a `logical` operator (its records are logical outcomes or
    #: logical Pauli frames, which composition uses), a `stabilizer`, or single `qubit`s
    role: str = "qubit"

    def to_json(self) -> dict:
        return {"name": self.name, "in": self.inp, "out": self.out, "kind": self.kind,
                "sign": self.sign, "role": self.role}


@dataclass
class Spec:
    """What an op promises, over ion names."""

    title: str
    inputs: list[str]                      # ions whose incoming state matters
    outputs: list[str]                     # ions that carry information out
    flows: list[Flow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {"title": self.title, "inputs": list(self.inputs), "outputs": list(self.outputs),
                "flows": [f.to_json() for f in self.flows], "notes": list(self.notes)}


def _symplectic(flow: Flow, inputs, outputs) -> int:
    """The flow as one GF(2) vector: (x_in, z_in, x_out, z_out)."""
    ii = {q: i for i, q in enumerate(inputs)}
    oo = {q: i for i, q in enumerate(outputs)}
    a, b = len(inputs), len(outputs)
    v = 0
    for q, p in flow.inp.items():
        if q not in ii:
            raise KeyError(f"flow {flow.name}: {q} is not a declared input")
        if p in ("X", "Y"):
            v |= 1 << ii[q]
        if p in ("Z", "Y"):
            v |= 1 << (a + ii[q])
    for q, p in flow.out.items():
        if q not in oo:
            raise KeyError(f"flow {flow.name}: {q} is not a declared output")
        if p in ("X", "Y"):
            v |= 1 << (2 * a + oo[q])
        if p in ("Z", "Y"):
            v |= 1 << (2 * a + b + oo[q])
    return v


def verify_flows(circuit: Circuit, spec: Spec, *, describe_record=None) -> dict:
    """Run the circuit on a Choi state and check every flow.  The report is JSON-able."""
    report = {"kind": "flows", "title": spec.title, "flows": [], "passed": False,
              "gates": circuit.counts()}
    rank = gf2.rank(_symplectic(f, spec.inputs, spec.outputs) for f in spec.flows)
    need = len(spec.inputs) + len(spec.outputs)
    report.update(rank=rank, needed=need, complete=rank == need)
    try:
        tab, n = simulate(circuit, choi=True)
    except NotClifford as e:
        report["error"] = str(e)
        return report
    missing = sorted({q for f in spec.flows for q in (*f.inp, *f.out)} - set(circuit.qubits))
    if missing:
        report["error"] = f"the circuit never names {missing[:6]}"
        return report
    idx = circuit.index
    ok_all = True
    for f in spec.flows:
        rx, rz, ny = pauli_bits(f.inp, lambda q: n + idx[q])
        sx, sz, _ = pauli_bits(f.out, lambda q: idx[q])
        val = tab.value(rx ^ sx, rz ^ sz)
        row = {"name": f.name, "kind": f.kind, "role": f.role}
        if val is None:
            row.update(ok=False, why="no definite relation: the circuit does not carry "
                                     "this operator to that one")
        else:
            const, var = val
            const ^= ny & 1                     # (Y)ᵀ = -Y on the reference
            recs = tab.as_records(var)
            if recs is None:
                row.update(ok=False, why="the relation depends on an outcome nobody "
                                         "recorded (a reset or discarded measurement)")
            elif f.kind == "exact" and (recs or const != f.sign):
                row.update(ok=False, records=recs, sign=const,
                           why="holds only up to a frame (records " + ", ".join(map(str, recs))
                               + f", sign {const}); the spec says exactly")
            elif f.kind == "measure" and (f.out or not recs):
                row.update(ok=False, records=recs, sign=const,
                           why="nothing is measured" if not recs else "output not identity")
            else:
                row.update(ok=True, records=recs, sign=const)
                if describe_record and recs:
                    row["where"] = [describe_record(r) for r in recs[:8]]
        ok_all &= row["ok"]
        report["flows"].append(row)
    report["passed"] = bool(ok_all and report["complete"])
    report["records"] = len(tab.records)
    report["detectors"] = sum(1 for r in tab.random if not r)
    return report
