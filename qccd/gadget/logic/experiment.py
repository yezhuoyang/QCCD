"""The reference experiment that measures a gadget's fault distance.

A gadget is fault-tolerant to distance d when no fewer than d faults *inside it* can
change the logical information it carries without some detector noticing.  To measure
that, the gadget is embedded in a circuit whose other parts are ideal:

    encoders   every input block starts as a code state whose every logical qubit is in a
               Bell pair with a reference qubit (the logical Choi state), built from noiseless
               Pauli-product measurements; a bare input qubit is Bell-paired directly
    gadget     its circuit, every operation a fault location
    decoders   noiseless measurements of every output block's stabilizers, then one noiseless
               product measurement per logical flow of the spec (reference Paulis ⊗ output
               logicals), which is deterministic exactly when the flow holds

A record parity that is deterministic in this circuit is a detector if it involves none
of the final logical measurements, and an observable otherwise.  Because the logical
information sits in Bell pairs with references, no parity that reveals logical information
can be deterministic without a logical measurement in it: the split is canonical, not a
choice of which parities to call detectors.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import gf2
from .circuit import Circuit
from .faults import low_weight_logical, propagate
from .tableau import NotClifford, simulate

__all__ = ["Block", "LogicalFlow", "Experiment", "build_experiment", "distance_report",
           "logical_pauli", "to_stim_experiment"]


@dataclass
class Block:
    """Code qubit i of `code` is ion `ions[i]`."""

    code: object
    ions: list[str]
    name: str = ""


@dataclass
class LogicalFlow:
    """Terms are `(block name, logical index, 'X'|'Y'|'Z')` for blocks, or
    `(ion, None, letter)` for a bare qubit."""

    name: str
    inp: list[tuple] = field(default_factory=list)
    out: list[tuple] = field(default_factory=list)


def logical_pauli(block: Block, j: int, letter: str) -> dict[str, str]:
    """A logical Pauli of a block as ion letters (Y is X·Z, supports multiplied)."""
    out: dict[str, str] = {}

    def mul(ion, p):
        a = out.get(ion, "I")
        table = {("I", "X"): "X", ("I", "Z"): "Z", ("X", "Z"): "Y", ("Z", "X"): "Y",
                 ("X", "X"): "I", ("Z", "Z"): "I", ("Y", "X"): "Z", ("Y", "Z"): "X"}
        r = table[(a, p)]
        if r == "I":
            out.pop(ion, None)
        else:
            out[ion] = r

    if letter in ("X", "Y"):
        for q in block.code.logical_support(j, "X"):
            mul(block.ions[q], "X")
    if letter in ("Z", "Y"):
        for q in block.code.logical_support(j, "Z"):
            mul(block.ions[q], "Z")
    return out


def _mul_into(acc: dict, pauli: dict) -> None:
    table = {"I": (0, 0), "X": (1, 0), "Z": (0, 1), "Y": (1, 1)}
    back = {v: k for k, v in table.items()}
    for q, p in pauli.items():
        a = table[acc.get(q, "I")]
        b = table[p]
        r = back[(a[0] ^ b[0], a[1] ^ b[1])]
        if r == "I":
            acc.pop(q, None)
        else:
            acc[q] = r


@dataclass
class Experiment:
    circuit: Circuit
    start: int                      # index of the gadget's first op
    detectors: list[int]            # record bitsets
    observables: list[int]
    logical_records: list[int]
    nondeterministic: list[int]     # logical records that came out random


def build_experiment(gadget: Circuit, *, blocks_in=(), qubits_in=(), blocks_out=(),
                     flows=()) -> Experiment:
    """The gadget between ideal encoders and decoders, with its detectors and observables."""
    bout = {b.name: b for b in blocks_out}
    exp = Circuit()
    refs: dict[tuple, str] = {}
    for b in blocks_in:
        exp.add("R", b.ions, noisy=False)
        exp.add("H", b.ions, noisy=False)
        for row in b.code.hz:
            exp.mpp({b.ions[q]: "Z" for q in gf2.support(row)}, label={"encode": b.name})
        for j in range(b.code.k):
            r = f"ref:{b.name}:{j}"
            refs[(b.name, j)] = r
            exp.add("R", [r], noisy=False)
            exp.add("H", [r], noisy=False)
            exp.mpp({r: "Z", **logical_pauli(b, j, "Z")}, label={"encode": b.name})
    for ion in qubits_in:
        r = f"ref:{ion}"
        refs[(ion, None)] = r
        exp.add("R", [r, ion], noisy=False)
        exp.add("H", [r], noisy=False)
        exp.add("CX", [r, ion], noisy=False)
    start = len(exp.ops)
    exp.extend(gadget, noisy=True)
    for b in blocks_out:
        for row in b.code.hx:
            exp.mpp({b.ions[q]: "X" for q in gf2.support(row)}, label={"decode": b.name})
        for row in b.code.hz:
            exp.mpp({b.ions[q]: "Z" for q in gf2.support(row)}, label={"decode": b.name})
    logical_recs = []
    for f in flows:
        pauli: dict[str, str] = {}
        for blk, j, letter in f.inp:
            _mul_into(pauli, {refs[(blk, j)]: letter})
        for blk, j, letter in f.out:
            _mul_into(pauli, logical_pauli(bout[blk], j, letter))
        if pauli:
            logical_recs.append(exp.mpp(pauli, label={"logical": f.name}))

    tab, _ = simulate(exp, choi=False)
    lmask = 0
    for k in logical_recs:
        lmask |= 1 << k
    not_det = [k for k in logical_recs if tab.random[k]]
    detectors, observables = [], []
    for k, rnd in enumerate(tab.random):
        if rnd:
            continue
        _, rec = tab.record_expr(k)
        v = rec | (1 << k)
        for o in observables:
            p = gf2.first_one(o & lmask)
            if (v >> p) & 1:
                v ^= o
        if v & lmask:
            observables.append(v)
        elif v:
            detectors.append(v)
    return Experiment(exp, start, detectors, observables, logical_recs, not_det)


def distance_report(gadget: Circuit, *, blocks_in=(), qubits_in=(), blocks_out=(),
                    flows=()) -> dict:
    """Embed the gadget between ideal encoders and decoders; find the fewest faults (1 or 2)
    that flip an observable without a detector.  JSON-able report."""
    report = {"kind": "distance", "gadget_ops": len(gadget.ops)}
    try:
        e = build_experiment(gadget, blocks_in=blocks_in, qubits_in=qubits_in,
                             blocks_out=blocks_out, flows=flows)
    except NotClifford as err:
        report["error"] = str(err)
        return report
    exp, detectors, observables = e.circuit, e.detectors, e.observables
    logical_recs, not_det = e.logical_records, e.nondeterministic
    prop = propagate(exp)
    witness = low_weight_logical(prop, detectors, observables)
    report.update(
        faults=len(prop.faults), detectors=len(detectors), observables=len(observables),
        logical_flows=len(logical_recs), nondeterministic_flows=len(not_det),
        at_least=3 if witness is None else None,
        distance=None if witness is None else len(witness),
        witness=[prop.faults[f].describe(exp) for f in (witness or [])],
    )
    report["passed"] = witness is None and not not_det and len(observables) == len(logical_recs)
    return report


def to_stim_experiment(e: Experiment, p: float = 0.001) -> str:
    """The experiment as a noisy stim circuit with its detectors and observables (for
    cross-checking the distance search against stim's)."""
    lines = [e.circuit.to_stim(noise=p)]
    total = len(e.circuit.records)

    def recs(v):
        return " ".join(f"rec[{r - total}]" for r in range(total) if (v >> r) & 1)

    for d in e.detectors:
        lines.append(f"DETECTOR {recs(d)}")
    for k, o in enumerate(e.observables):
        lines.append(f"OBSERVABLE_INCLUDE({k}) {recs(o)}")
    return "\n".join(lines)
