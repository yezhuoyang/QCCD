"""Devirtualize a `.sysprog` on a finite QCCD machine (slice S1: one flat compile).

FormalRV's `.sysprog` names VIRTUAL qubits and asks the machine for resources by system call.
Here each one becomes something a trapped-ion machine does:

  data[i]                 a fixed physical qubit (an ion) for the whole run
  request_ancilla N       N ions from the pool, each `reset` (prepared in |0>)
  release_ancilla         the ions go back to the pool (nothing is emitted)
  route ...               nothing in the circuit: the compiler's transport realizes it, and every
                          transport instruction is attributed to the route that asked for it
  H S X Z Sdg / CNOT CZ   the same gate on the ions holding those qubits, in the same order
  measure basis=X|Z       (H then) a measurement into the same record bit
  request_magic |Y+>      QCCD's factory: an encoder for the block's own stabilizers and Y-bar,
                          synthesized by stim and checked to give exactly that state (distance 1
                          by design -- an injection, not a distillation)
  decode_syndrome, pauli_frame_update
                          classical: no ion instruction; kept on the timeline as classical events

The ancilla pool is register allocation by lifetime, in TIME order (a released ion may serve a
later request).  The circuit goes to qccdc, which keeps every qubit's gate order and stamps each
hardware instruction with the circuit operations it serves (`meta.op`).  So every hardware
instruction maps to circuit ops, every circuit op to the `.sysprog` event it came from, and
`provenance` takes it up the layers.

This slice compiles the whole program at once.  It is exact, and it does not scale: the next
slice compiles each gadget once per tile and composes (docs/FULLSTACK_PLAN.md §2.3).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import sysprog as sp

QASM_1Q = {"H": "h", "S": "s", "X": "x", "Z": "z", "Sdg": "sdg"}
QASM_2Q = {"CNOT": "cx", "CZ": "cz"}
CLASSICAL = ("decode_syndrome", "pauli_frame_update", "parity", "release_ancilla", "route")


class Refused(ValueError):
    pass


@dataclass
class CircuitOp:
    index: int                   # position in the emitted circuit (qccdc's `meta.op`)
    text: str                    # the QASM statement
    event: int                   # the `.sysprog` event uid it implements
    role: str                    # 'gate' | 'reset' | 'measure' | 'basis' | 'factory'


@dataclass
class Devirtualized:
    expansion: sp.Expansion
    phys: dict                   # (reg, idx) virtual -> physical qubit, for every virtual qubit used
    n_qubits: int
    ops: list                    # [CircuitOp]
    qasm: str
    factory: dict                # what the factory implementation is and the check it passed
    routes_for: dict = field(default_factory=dict)  # physical qubit -> [(time, route event uid)]

    def event_of_op(self, i: int) -> int:
        return self.ops[i].event


# ----------------------------------------------------------------------------- the factory

def code_from_qec(qec_stage) -> dict:
    """The first code of a `.qec` stage's `codes:` block: stabilizer rows and logicals."""
    codes = qec_stage.unit("codes")
    if codes is None:
        raise Refused("the .qec has no codes block")
    blk = codes.body[0]
    n = int(blk.text.split("[[", 1)[1].split(",", 1)[0])
    stabs, lx, lz = [], None, None
    for row in blk.children:
        t = row.text.split()
        if t[0] == "stab":
            stabs.append(t[1])
        elif t[0].startswith("logX"):
            lx = t[1]
        elif t[0].startswith("logZ"):
            lz = t[1]
    return {"name": blk.text.split()[0], "n": n, "stabs": stabs, "logX": lx, "logZ": lz}


def _pauli(text: str, n: int):
    import re
    import stim
    s = ["_"] * n
    for p, q in re.findall(r"([XYZ])\[(\d+)\]", text):
        s[int(q)] = p
    return stim.PauliString("".join(s))


def y_factory(code: dict) -> tuple[list, dict]:
    """Gates (stim names, local qubits 0..n-1) preparing the encoded |Y+> from |0..0>, and the
    check: every stabilizer and Y-bar = i X-bar Z-bar have expectation +1 on the output."""
    import stim
    n = code["n"]
    stabs = [_pauli(s, n) for s in code["stabs"]]
    xbar, zbar = _pauli(code["logX"], n), _pauli(code["logZ"], n)
    ybar = (1j) * xbar * zbar
    if ybar.sign not in (1, -1):
        raise Refused("Y-bar is not Hermitian for this code's logicals")
    tab = stim.Tableau.from_stabilizers(stabs + [ybar], allow_redundant=False, allow_underconstrained=False)
    circ = tab.to_circuit(method="elimination")
    gates = []
    for inst in circ:
        name = inst.name
        ts = [t.value for t in inst.targets_copy()]
        arity = 2 if name in ("CX", "CZ", "SWAP") else 1
        for k in range(0, len(ts), arity):
            gates.append((name, tuple(ts[k:k + arity])))
    sim = stim.TableauSimulator()
    sim.set_num_qubits(n)
    for name, qs in gates:
        sim.do(stim.Circuit(f"{name} " + " ".join(map(str, qs))))
    ok = all(sim.peek_observable_expectation(s) == 1 for s in stabs) and \
        sim.peek_observable_expectation(ybar) == 1
    if not ok:
        raise Refused("the synthesized |Y+> encoder does not give the promised state")
    return gates, {"kind": "encoder", "state": "|Y+>", "code": code["name"], "n": n,
                   "gates": len(gates), "check": "every stabilizer and Y-bar = +1 (stim tableau)",
                   "distance": "1 (a unitary encoder: an injection, not a distillation)"}


STIM_TO_QASM = {"H": "h", "S": "s", "S_DAG": "sdg", "X": "x", "Z": "z", "CX": "cx", "CZ": "cz",
                "SWAP": "swap", "SQRT_X": "sx", "I": "id"}


# ----------------------------------------------------------------------------- devirtualize

def devirtualize(expansion: sp.Expansion, code: dict) -> Devirtualized:
    events = sorted(expansion.events, key=lambda e: (e.start, e.uid))
    phys: dict = {}
    live: dict = {}                       # virtual -> physical, currently allocated
    free: list = []                       # released physical qubits (LIFO: reuse the most recent)
    next_q = 0
    n_data = 0
    for e in expansion.events:            # data cells are initial residents
        for r, i in e.qubits + e.work:
            if r == "data":
                n_data = max(n_data, i + 1)
    for i in range(n_data):
        phys[("data", i)] = live[("data", i)] = i
    next_q = n_data
    ops: list[CircuitOp] = []
    lines = []
    routes_for: dict = {}
    phantom: set = set()                  # granted factory workspace that holds no ion
    fgates, fac_info = None, {}

    def q(v, e):
        if v not in live:
            raise Refused(f"line {e.node.line}: {v[0]}[{v[1]}] is used but not allocated")
        return live[v]

    def emit(text, ev, role):
        ops.append(CircuitOp(len(ops), text, ev.uid, role))
        lines.append(text)

    for e in events:
        k = e.kind
        if k == "request_ancilla":
            for v in e.qubits:
                if v in live:
                    raise Refused(f"line {e.node.line}: {v} requested twice")
                if free:
                    p = free.pop()
                else:
                    p = next_q
                    next_q += 1
                live[v] = p
                phys.setdefault(v, p)
                emit(f"reset q[{p}];", e, "reset")
        elif k == "release_ancilla":
            for v in e.qubits:
                if v in phantom:
                    phantom.discard(v)
                    continue
                free.append(q(v, e))
                del live[v]
        elif k in QASM_1Q:
            for v in e.qubits:
                emit(f"{QASM_1Q[k]} q[{q(v, e)}];", e, "gate")
        elif k in QASM_2Q:
            a, b = e.qubits
            emit(f"{QASM_2Q[k]} q[{q(a, e)}],q[{q(b, e)}];", e, "gate")
        elif k == "CY":
            a, b = e.qubits
            pa, pb = q(a, e), q(b, e)
            emit(f"sdg q[{pb}];", e, "gate")
            emit(f"cx q[{pa}],q[{pb}];", e, "gate")
            emit(f"s q[{pb}];", e, "gate")
        elif k == "measure":
            (v,) = e.qubits
            p = q(v, e)
            if e.arg == "X":
                emit(f"h q[{p}];", e, "basis")
            (reg, bit), = e.writes
            emit(f"measure q[{p}] -> {'c' if reg == 'c' else 'syn'}[{bit}];", e, "measure")
        elif k == "request_magic":
            if e.arg != "|Y+>":
                raise Refused(f"line {e.node.line}: no QCCD factory for {e.arg} yet")
            if fgates is None:
                fgates, fac_info = y_factory(code)
                fac_info["workspace_granted"] = len(e.work)
                fac_info["workspace_used"] = 0
            # the listing grants the factory workspace for its production interval; this
            # implementation needs none, so those cells hold no ion (no ion is spent on them)
            for v in e.work:
                if v in live or v in phantom:
                    raise Refused(f"line {e.node.line}: workspace {v} already allocated")
                phantom.add(v)
            out = [q(v, e) for v in e.qubits]
            if len(out) != code["n"]:
                raise Refused(f"line {e.node.line}: the factory outputs {code['n']} qubits, asked {len(out)}")
            for name, qs in fgates:
                if name not in STIM_TO_QASM:
                    raise Refused(f"factory gate {name} has no QASM spelling")
                emit(f"{STIM_TO_QASM[name]} " + ",".join(f"q[{out[x]}]" for x in qs) + ";", e, "factory")
        elif k == "route":
            for v in e.qubits:
                routes_for.setdefault(q(v, e), []).append((e.start, e.uid))
        elif k in CLASSICAL:
            pass
        else:
            raise Refused(f"line {e.node.line}: no QCCD meaning for {k}")
    if live.keys() - {("data", i) for i in range(n_data)}:
        left = sorted(live.keys() - {("data", i) for i in range(n_data)})[:6]
        raise Refused(f"ancillas still allocated at the end: {left}")
    n_c = max([b + 1 for e in expansion.events for r, b in e.writes if r == "c"] + [1])
    n_s = max([b + 1 for e in expansion.events for r, b in e.writes if r == "syndrome"] + [1])
    qasm = "\n".join(["OPENQASM 2.0;", 'include "qelib1.inc";', f"qreg q[{next_q}];",
                      f"creg c[{n_c}];", f"creg syn[{n_s}];"] + lines) + "\n"
    return Devirtualized(expansion, phys, next_q, ops, qasm, fac_info, routes_for)
