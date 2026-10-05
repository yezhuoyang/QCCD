"""A compiled hardware program (TSIR) as a noisy stim circuit.

The circuit is read off the program itself, in program order: every `gate`, `measure` and
`reset` instruction becomes its stabilizer operation on the ions it names (ions keep their
identity through every move, so nothing a shuttle does changes which qubit a later gate
touches), and every instruction's replayed duration becomes idle noise on every ion.
Noise comes from `qccd.qec.noise`, evaluated on the SAME per-cycle view the rule checker
judges (`replay(on_cycle=...)`), so the n̄ that prices an MS gate here is the n̄ R7 caps.

**Pinning measurements without a certificate.**  A memory experiment's detectors are
declared on the source circuit's classical bits.  The k-th measurement of circuit qubit q
in the program is the k-th `measure q[..] -> c[b]` of q in the source -- a compiler may
reorder operations on different wires, never two measurements of one -- so bit b is that
measurement's record.  The qubit -> ion map comes from `prog.meta["qubit_map"]`, else the
certificate's `map`, else the convention that circuit qubit i is ion `q{i}`.  A
certificate, when given, is only cross-checked against this (`meta.op` -> `circuit_ops`),
never believed: the answer must not depend on a claim the grader cannot check.

**An appended readout** (`MemoryExperiment.readout == "appended"`): the source stops
after its last syndrome round and the grader takes the final data readout itself, after
the program's last instruction, on the ions that hold the data qubits -- a flip of
probability 1 − F_m (the `measure` channel), then the measurement, in the memory's basis --
and charges one measurement's duration (`primitives.measure.us`) as idle on every ion.
This exists because the reference compiler's rigid rotation cannot measure an ion that
rides the loop, so no data readout it could emit would pass the rules on a loop device.
It is an approximation to revisit: the readout's transport and the parallelism of a real
readout are not modelled.

A program that cannot be read as a circuit -- a non-Clifford angle, an ion that holds no
circuit qubit, a qubit measured a different number of times than the source measures it,
a program the replay cannot execute -- raises `ExtractionError`.  A program that CAN be
read but computes the wrong thing extracts fine; `qccd.qec.check.noiseless_check` is what
reports it.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .experiments import MemoryExperiment, append_readout
from .native import (ABSTRACT_2Q, NotClifford, canonical_abstract, emit_abstract,
                     emit_ms, emit_r, emit_vz)
from .noise import CHANNEL_NAMES, NoiseModel, get as get_noise
from .qasm import parse_qasm
from .stimtext import StimText, declare

__all__ = ["Extraction", "ExtractionError", "extract", "load_program", "load_arch",
           "resolve_qubit_map"]


class ExtractionError(ValueError):
    """The program cannot be read as a circuit of the experiment (see module docstring)."""


@dataclass
class Extraction:
    """The noisy circuit, where its noise came from, and what the replay saw."""

    circuit: Any                                  # stim.Circuit
    budget: dict[str, float]
    stats: dict[str, Any]
    params: dict[str, Any] = field(default_factory=dict)
    qubit_map: dict[int, str] = field(default_factory=dict)
    #: the stim text as it was written, line by line, and which lines each hardware
    #: instruction produced: `{"id", "type", "a", "b", "dt_us", "why"}` with `lines[a:b]`
    #: that instruction's operations and noise, and `why` the numbers each noise line was
    #: priced from (n-bar, chain length, duration).  `id` is None for the preparation, the
    #: appended readout and the detector declarations.  A page shows a reader exactly how
    #: a shuttle, a gate or a wait became noise from this; nothing reads it back.
    lines: list[str] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)


# ------------------------------------------------------------------ inputs


def load_program(prog):
    """A `TSIR`, from a `TSIR`, a path to a `.tsir.json`, or its parsed document."""
    from ..ir.tsir import TSIR

    if isinstance(prog, TSIR):
        return prog
    if isinstance(prog, (str, Path)):
        return TSIR.load(prog)
    if isinstance(prog, Mapping):
        return TSIR.from_json(prog)
    raise TypeError(f"not a program: {type(prog).__name__}")


def load_arch(arch):
    """An `Architecture`, from one, a path to an `.arch.json`, or its parsed document."""
    from ..arch import Architecture

    if isinstance(arch, Architecture):
        return arch
    if isinstance(arch, (str, Path)):
        return Architecture.from_json(json.loads(Path(arch).read_text(encoding="utf-8")))
    if isinstance(arch, Mapping):
        return Architecture.from_json(arch)
    raise TypeError(f"not an architecture: {type(arch).__name__}")


def resolve_qubit_map(prog, qubit_map=None, cert: Mapping | None = None,
                      n_qubits: int | None = None) -> tuple[dict[int, str], str]:
    """Circuit qubit -> ion, and where the map came from.

    Accepts `{"0": "q0", ...}`, `{0: "q0"}` or a list indexed by qubit.
    """
    src, origin = None, "identity"
    if qubit_map is not None:
        src, origin = qubit_map, "argument"
    elif isinstance(prog.meta, Mapping) and prog.meta.get("qubit_map") is not None:
        src, origin = prog.meta["qubit_map"], "meta.qubit_map"
    elif cert is not None and cert.get("map") is not None:
        src, origin = cert["map"], "certificate"
    if src is None:
        n = n_qubits if n_qubits is not None else len(prog.ion_names())
        return {i: f"q{i}" for i in range(n)}, origin
    if isinstance(src, Mapping):
        out = {int(k): str(v) for k, v in src.items()}
    else:
        out = {i: str(v) for i, v in enumerate(src)}
    if len(set(out.values())) != len(out):
        raise ExtractionError(f"the qubit map ({origin}) sends two qubits to one ion")
    return out, origin


# ------------------------------------------------------------------ extraction


def _param(ins, k: int, n: int, width: int) -> tuple[float, ...]:
    """The parameter tuple of operand k: one per operand, or one shared by all."""
    ps = ins.params
    if not ps:
        raise ExtractionError(f"instruction {ins.id}: {ins.gate} carries no angle")
    p = ps[k] if len(ps) == n else (ps[0] if len(ps) == 1 else None)
    if p is None:
        raise ExtractionError(f"instruction {ins.id}: {len(ps)} parameter tuples for "
                              f"{n} operands")
    if len(p) < width:
        raise ExtractionError(f"instruction {ins.id}: {ins.gate} needs {width} angle(s), "
                              f"got {list(p)}")
    return tuple(p)


def _pairs(ins) -> list[tuple[str, str]]:
    if ins.pairs:
        return [tuple(p) for p in ins.pairs]  # type: ignore[misc]
    if len(ins.ions) == 2 and ins.arity != 1:
        return [(ins.ions[0], ins.ions[1])]
    raise ExtractionError(f"instruction {ins.id}: two-qubit {ins.gate} names no pair")


def extract(prog, arch, exp_or_spec, qasm: str | None = None, *,
            noise: NoiseModel | str | None = None, qubit_map=None,
            cert: Mapping | None = None, table: str | None = None) -> Extraction:
    """TSIR + architecture + experiment -> `Extraction` (noisy circuit, budget, stats).

    `exp_or_spec` is a `MemoryExperiment` or its `spec()` document; `qasm` overrides (or,
    for a spec, supplies) the source circuit.  `table` overrides the noise model's
    operating-point table.
    """
    from ..cost.models import corrected_model
    from ..verify import verify

    prog = load_program(prog)
    arch = load_arch(arch)
    if isinstance(exp_or_spec, MemoryExperiment):
        exp = exp_or_spec
    else:
        exp = MemoryExperiment.from_spec(exp_or_spec, qasm or "")
    text = qasm if qasm is not None else exp.qasm
    if not text:
        raise ExtractionError("no source circuit: pass the experiment's QASM")
    src = parse_qasm(text)
    bits_of = src.bits_by_qubit()

    nm = get_noise(noise) if isinstance(noise, str) else (noise or get_noise("qccd-noise@1"))
    params = nm.resolve(arch)
    model = corrected_model(table or nm.table)
    if table:
        params["table"] = table

    # -- one replay: what each instruction saw, and how long it took
    first: dict[int, tuple[dict, dict, dict]] = {}
    dur: dict[int, float] = defaultdict(float)

    def on_cycle(v) -> None:
        iid = v.instr.id
        dur[iid] += v.duration_us
        if iid not in first:
            first[iid] = (dict(v.quanta), dict(v.pos_before), dict(v.occ_before))

    # `verify`, not bare `replay`: one replay gives both the per-cycle views the noise is
    # priced from and the rule verdicts the evaluator would give (R4, R7c, R9, R11 too)
    try:
        report = verify(prog, arch, model, on_cycle=on_cycle)
    except Exception as exc:  # ReplayError, and whatever a malformed program trips over
        raise ExtractionError(f"the program does not replay on {arch.name}: "
                              f"{type(exc).__name__}: {exc}") from exc
    res = report.result

    # -- qubits: circuit qubit q is stim qubit q; ions holding no circuit qubit follow
    ions: list[str] = []
    for ins in prog.instructions:
        if ins.type == "init":
            ions += [i for i in ins.placement if i not in ions]
    qmap, map_origin = resolve_qubit_map(prog, qubit_map, cert, src.n_qubits)
    missing = sorted(q for q in range(src.n_qubits) if q not in qmap)
    if missing:
        raise ExtractionError(f"the qubit map ({map_origin}) places no ion for circuit "
                              f"qubit(s) {missing[:8]}")
    unplaced = sorted({ion for q, ion in qmap.items() if q < src.n_qubits and ion not in ions})
    if unplaced:
        raise ExtractionError(f"the qubit map ({map_origin}) names ion(s) {unplaced[:8]} "
                              f"that the program never places")
    sq: dict[str, int] = {ion: q for q, ion in qmap.items() if q < src.n_qubits}
    for ion in ions:
        if ion not in sq:
            sq[ion] = src.n_qubits + sum(1 for i in sq if sq[i] >= src.n_qubits)
    everyone = sorted(sq.values())
    qubit_of_ion = {ion: q for ion, q in sq.items() if q < src.n_qubits}

    # -- the optional certificate cross-check: meta.op -> circuit_ops (qubit, ordinal)
    ordinal_of_op: dict[int, tuple[int, int]] = {}
    if cert is not None and cert.get("circuit_ops"):
        seen_q: dict[int, int] = defaultdict(int)
        for o in cert["circuit_ops"]:
            if o.get("name") == "measure" and o.get("qubits"):
                q = int(o["qubits"][0])
                ordinal_of_op[int(o["i"])] = (q, seen_q[q])
                seen_q[q] += 1
    disagreements: list[str] = []

    c = StimText()
    trace: list[dict[str, Any]] = []
    why: list[str] = []
    c.append("R", everyone)
    trace.append({"id": None, "type": "prepare", "a": 0, "b": len(c.lines), "dt_us": 0.0,
                  "why": ["every ion starts in |0>"]})
    budget = {k: 0.0 for k in CHANNEL_NAMES}
    count: dict[int, int] = defaultdict(int)
    meas_of_bit: dict[int, int] = {}
    n_meas = 0
    n = {"ms": 0, "1q": 0, "vz": 0, "measure": 0, "reset": 0, "cool": 0}
    nbars: list[float] = []
    chains: list[int] = []

    def ms_noise(a: str, b: str, view) -> None:
        quanta, pos, occ = view
        nbar = max(float(quanta.get(a, 0.0)), float(quanta.get(b, 0.0)))
        n_chain = int(occ.get(pos.get(a), 2) or 2)
        e = nm.ms_error(model, arch, nbar, n_chain)
        c.append("DEPOLARIZE2", [sq[a], sq[b]], e.total)
        budget["ms_base"] += e.base
        budget["ms_heating"] += e.heating
        budget["ms_chain"] += e.chain
        why.append(f"MS {a},{b}: n-bar {nbar:.3g}, chain of {n_chain} -> p = {e.total:.3g} "
                   f"(base {e.base:.3g} + heating {e.heating:.3g} + chain {e.chain:.3g})")
        nbars.append(nbar)
        chains.append(n_chain)
        n["ms"] += 1

    def q1_noise(ion: str) -> None:
        p = nm.p_1q(params)
        c.append("DEPOLARIZE1", [sq[ion]], p)
        budget["gate_1q"] += p
        n["1q"] += 1
        if not why or not why[-1].startswith("one-qubit"):
            why.append(f"one-qubit gate: p = {p:.3g} each (1 - fidelity)")

    for ins in prog.instructions:
        view = first.get(ins.id, ({}, {}, {}))
        line0 = len(c.lines)
        why = []
        try:
            unknown = [i for i in (list(ins.ions) + [x for p in ins.pairs for x in p])
                       if i not in sq]
            if unknown and ins.type in ("gate", "measure", "reset"):
                raise ExtractionError(f"instruction {ins.id} names unplaced ion(s) {unknown[:5]}")
            if ins.type == "gate":
                g = str(ins.gate)
                if g == "MS":
                    prs = _pairs(ins)
                    for k, (a, b) in enumerate(prs):
                        emit_ms(c, sq[a], sq[b], _param(ins, k, len(prs), 1)[0])
                        ms_noise(a, b, view)
                elif g in ("R", "VZ"):
                    if ins.pairs:
                        raise ExtractionError(f"instruction {ins.id}: {g} is a one-qubit "
                                              f"gate but carries pairs")
                    for k, ion in enumerate(ins.ions):
                        if g == "R":
                            th, ph = _param(ins, k, len(ins.ions), 2)[:2]
                            emit_r(c, sq[ion], th, ph)
                            q1_noise(ion)
                        else:
                            emit_vz(c, sq[ion], _param(ins, k, len(ins.ions), 1)[0])
                            n["vz"] += 1
                else:
                    key = canonical_abstract(g)
                    if key in ABSTRACT_2Q:
                        for a, b in _pairs(ins):
                            emit_abstract(c, key, [sq[a], sq[b]])
                            ms_noise(a, b, view)
                    else:
                        if ins.pairs:
                            raise ExtractionError(f"instruction {ins.id}: {g} is a one-qubit "
                                                  f"gate but carries pairs")
                        for ion in ins.ions:
                            emit_abstract(c, key, [sq[ion]])
                            if key != "I":
                                q1_noise(ion)
            elif ins.type == "measure":
                targets = [sq[i] for i in ins.ions]
                p = nm.p_measure(params)
                c.append("X_ERROR", targets, p)
                c.append("M", targets)
                budget["measure"] += p * len(targets)
                why.append(f"readout: flip with p = {p:.3g} before each measurement (1 - fidelity)")
                pinned = set()
                for ion in ins.ions:
                    if ion not in qubit_of_ion:
                        raise ExtractionError(f"instruction {ins.id} measures ion {ion}, "
                                              f"which holds no circuit qubit")
                    q = qubit_of_ion[ion]
                    k = count[q]
                    count[q] += 1
                    bits = bits_of.get(q, [])
                    if k >= len(bits):
                        raise ExtractionError(f"the program measures circuit qubit {q} "
                                              f"(ion {ion}) more than the {len(bits)} time(s) "
                                              f"the source circuit does")
                    meas_of_bit[bits[k]] = n_meas
                    n_meas += 1
                    pinned.add((q, k))
                n["measure"] += len(targets)
                if ordinal_of_op:
                    claimed = {ordinal_of_op[int(o)] for o in (ins.meta or {}).get("op", ())
                               if int(o) in ordinal_of_op}
                    if claimed != pinned:
                        disagreements.append(f"instruction {ins.id}: certificate says "
                                             f"{sorted(claimed)}, order says {sorted(pinned)}")
            elif ins.type == "reset":
                targets = [sq[i] for i in ins.ions]
                p = nm.p_reset(params)
                c.append("R", targets)
                c.append("X_ERROR", targets, p)
                budget["reset"] += p * len(targets)
                n["reset"] += len(targets)
                why.append(f"reset: left in |1> with p = {p:.3g}")
            elif ins.type == "cool":
                n["cool"] += 1
        except NotClifford as exc:
            raise ExtractionError(f"instruction {ins.id}: {exc}") from exc
        dt = dur.get(ins.id, 0.0)
        pz = nm.p_idle(params, dt)
        if pz > 0:
            c.append("Z_ERROR", everyone, pz)
            budget["idle"] += pz * len(everyone)
            why.append(f"takes {dt:.4g} us: every ion dephases with p = {pz:.3g} "
                       f"((1 - exp(-dt/T2)) / 2)")
        trace.append({"id": ins.id, "type": ins.type, "a": line0, "b": len(c.lines),
                      "dt_us": float(dt), "why": why})

    short = [f"qubit {q}: {count[q]} of {len(b)}" for q, b in sorted(bits_of.items())
             if count[q] != len(b)]
    if short:
        raise ExtractionError("the program measures some circuit qubits fewer times than the "
                              "source circuit does (" + "; ".join(short[:6]) + ")")

    # -- the appended data readout (see the module docstring)
    n_appended = 0
    line0 = len(c.lines)
    if exp.readout == "appended":
        taken = [b for q, b in exp.readout_bits if b in meas_of_bit]
        if taken:
            raise ExtractionError(f"the source circuit itself writes the appended readout "
                                  f"bit(s) {taken[:6]}")
        p = nm.p_measure(params)
        for q, b in exp.readout_bits:
            if q not in qmap or qmap[q] not in sq:
                raise ExtractionError(f"no ion holds data qubit {q} for the appended readout")
            append_readout(c, exp.basis, [sq[qmap[q]]], p)
            meas_of_bit[b] = n_meas
            n_meas += 1
            n_appended += 1
        budget["measure"] += p * n_appended
        dt = float(arch.primitives.scalar("measure").get("us", 0.0))
        pz = nm.p_idle(params, dt)
        if pz > 0:
            c.append("Z_ERROR", everyone, pz)
            budget["idle"] += pz * len(everyone)
    if len(c.lines) > line0:
        trace.append({"id": None, "type": "readout", "a": line0, "b": len(c.lines), "dt_us": 0.0,
                      "why": ["the grader reads the data out after the last instruction, in the "
                              "memory's basis, with the readout error and one measurement's idle"]})
    line0 = len(c.lines)
    try:
        declare(c, exp.detectors, exp.observables, meas_of_bit, n_meas)
    except KeyError as exc:
        raise ExtractionError(str(exc)) from exc
    trace.append({"id": None, "type": "detectors", "a": line0, "b": len(c.lines), "dt_us": 0.0,
                  "why": ["the experiment's detectors and logical observable, over the "
                          "measurement records"]})
    circuit = c.circuit()

    failed = sorted(report.rules.failed())
    stats = {
        "n_ms": n["ms"],
        "n_1q": n["1q"],
        "n_vz": n["vz"],
        "n_measure": n["measure"],
        "n_reset": n["reset"],
        "n_cool": n["cool"],
        "n_readout_appended": n_appended,
        "n_instructions": len(prog.instructions),
        "nbar_max": max(nbars, default=0.0),
        "nbar_mean_at_ms": (sum(nbars) / len(nbars)) if nbars else 0.0,
        "chain_max": max(chains, default=0),
        "t_us": float(res.total_us),
        "t_readout_us": (float(arch.primitives.scalar("measure").get("us", 0.0))
                         if n_appended else 0.0),
        "ions": len(everyone),
        "qubits": src.n_qubits,
        "detectors": circuit.num_detectors,
        "observables": circuit.num_observables,
        "qubit_map": map_origin,
        "rules_failed": failed,
        "rule_violations": report.rules.by_rule(),
        "cert_crosscheck": ("not provided" if not ordinal_of_op
                            else ("agrees" if not disagreements
                                  else "disagrees: " + "; ".join(disagreements[:4]))),
    }
    return Extraction(circuit=circuit, budget=budget, stats=stats, params=params,
                      qubit_map={q: ion for q, ion in qmap.items() if q < src.n_qubits},
                      lines=list(c.lines), trace=trace)
