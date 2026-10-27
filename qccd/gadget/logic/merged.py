"""The exact output distribution of a program with T states and guarded corrections.

`contract.run_lines` follows every branch of a program, which is 2^k for k measurements;
`symbolic.py` decides 2^k branches at once but only for Clifford programs.  A program lowered
from FormalRV's `.qec` has both: dozens of measurements, and T states.  But each measurement's
branches CONVERGE: once the corrections that read an outcome have been applied, the branches
differ by a global phase and nothing later reads the outcome.  So here the branches are
followed with a state vector each, and after every instruction

  * an outcome no later guard reads (and that is not a program output) is forgotten,
  * a block nothing later names, once unentangled from the rest, is traced out (a guarded
    readout that did not fire leaves its block behind),
  * branches with the same remembered outcomes and the same state up to a global phase are
    merged (their probabilities add).

The live branch count stays at 2^(outcomes still awaited by a guard), not 2^(measurements).
The result is the exact distribution of the chosen outcomes, every branch accounted for.
"""
from __future__ import annotations

import cmath
import math

from ..algorithm import Algorithm
from .contract import State, _primitive

_EPS = 1e-10


class TooManyBranches(RuntimeError):
    pass


def _canon(st: State) -> tuple:
    """The state as a hashable value, qubits by name, up to a global phase."""
    order = sorted(range(len(st.names)), key=lambda i: st.names[i])
    perm = {i: j for j, i in enumerate(order)}

    def remap(b):
        out = 0
        for i in range(len(st.names)):
            if b >> i & 1:
                out |= 1 << perm[i]
        return out
    items = [(remap(b), a) for b, a in st.amp.items() if abs(a) > 1e-9]
    if not items:
        return (tuple(sorted(st.names)), ())
    ref = max(items, key=lambda x: (round(abs(x[1]), 9), -x[0]))[1]
    ph = ref / abs(ref)
    vals = tuple(sorted((b, round((a / ph).real, 7), round((a / ph).imag, 7)) for b, a in items))
    return (tuple(sorted(st.names)), vals)


def _factor_out(st: State, q: str) -> bool:
    """Trace out q if it is in a product state with the rest; False when it is entangled."""
    k = st.bit(q)
    pairs: dict[int, list] = {}
    for b, a in st.amp.items():
        rest = (b & ((1 << k) - 1)) | ((b >> (k + 1)) << k)
        pairs.setdefault(rest, [0j, 0j])[(b >> k) & 1] += a
    phi = None
    for v in pairs.values():
        n = math.sqrt(abs(v[0]) ** 2 + abs(v[1]) ** 2)
        if n < _EPS:
            continue
        w = (v[0] / n, v[1] / n)
        if phi is None:
            phi = w
            continue
        # proportional up to a phase?
        ov = abs(w[0] * phi[0].conjugate() + w[1] * phi[1].conjugate())
        if abs(ov - 1) > 1e-7:
            return False
    if phi is None:
        return False
    new: dict[int, complex] = {}
    for rest, v in pairs.items():
        amp = v[0] * phi[0].conjugate() + v[1] * phi[1].conjugate()
        if abs(amp) > _EPS:
            new[rest] = amp
    st.amp = new
    st.names.pop(k)
    return True


def distribution(alg: Algorithm, keep: list[str], *, max_branches: int = 4096) -> dict:
    """{tuple of `keep` outcomes: probability}, exactly, for the expanded program `alg`."""
    ins_list = alg.instructions
    # the last instruction that reads each outcome, and that names each block: after it, the
    # outcome is forgotten and the block traced out (one pass -- a program of half a million
    # instructions names tens of thousands of blocks, and a set per instruction would not fit)
    last_read: dict = {}
    last_name: dict = {}
    for i, ins in enumerate(ins_list):
        for x in (ins.cond.names if ins.cond else ()):
            last_read[x] = i
        for b in ins.blocks:
            last_name[b] = i
    keep_set = set(keep)
    branches: list = [(State(), {}, 1.0)]
    peak = 1
    for i, ins in enumerate(ins_list):
        word, args = _words(ins)
        out = []
        for st, oc, p in branches:
            if ins.cond is not None and not ins.cond.holds(oc):
                out.append((st, oc, p))
                continue
            if ins.kind in ("ppm", "zz", "xx", "read"):
                for o in (0, 1):
                    s2 = st.copy()
                    pr = _primitive(s2, word, args, o)
                    if pr and pr > _EPS:
                        oc2 = dict(oc)
                        if ins.cvar:
                            oc2[ins.cvar] = o
                        out.append((s2, oc2, p * pr))
            else:
                s2 = st.copy()
                _primitive(s2, word, args)
                out.append((s2, oc, p))
        # forget, trace out, merge
        merged: dict = {}
        for st, oc, p in out:
            oc = {k: v for k, v in oc.items() if k in keep_set or last_read.get(k, -1) > i}
            for q in [q for q in list(st.names) if last_name.get(q, -1) <= i]:
                _factor_out(st, q)
            key = (tuple(sorted(oc.items())), _canon(st))
            if key in merged:
                merged[key] = (merged[key][0], merged[key][1], merged[key][2] + p)
            else:
                merged[key] = (st, oc, p)
        branches = list(merged.values())
        peak = max(peak, len(branches))
        if len(branches) > max_branches:
            raise TooManyBranches(f"more than {max_branches} live branches at instruction {i}")
    dist: dict = {}
    states: dict = {}
    for st, oc, p in branches:
        k = tuple(oc.get(x) for x in keep)
        dist[k] = dist.get(k, 0.0) + p
        states.setdefault(k, []).append(st)
    return {"outcomes": list(keep), "distribution": {k: round(v, 9) for k, v in sorted(dist.items())},
            "peak_branches": peak, "instructions": len(ins_list), "states": states}


def _words(ins) -> tuple[str, list]:
    """The primitive and its arguments, as `contract._primitive` reads them."""
    k = ins.kind
    if k == "prep":
        return "prep", [ins.blocks[0], ins.basis]
    if k == "read":
        return "read", [ins.blocks[0], ins.basis]
    if k == "ppm":
        return "ppm", [x for p, b in zip(ins.paulis, ins.blocks) for x in (p, b)]
    if k in ("zz", "xx", "cx"):
        return k, list(ins.blocks)
    return k, list(ins.blocks)
