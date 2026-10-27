"""A compiled hardware program (TSIR) as a noisy stim circuit, written by the noise model's text.

The circuit is what the noise model's evaluation text says it is (`qccd.evalset`, the files
`qccd/evalset/library/qccd-noise@1.eval` and `qccd-noise@2.eval`): the programme is replayed
once on its device, every gate, measurement and reset becomes its stabilizer operation on the
ions it names (an ion keeps its qubit through every move, so transport writes no gate), and
every channel of the text puts its Pauli noise where its clause says, with the probability its
term gives.  Nothing here prices anything: change the text and the circuit changes with it.
The Evaluation page prints the texts, and the hand-written extraction this replaced is kept in
`legacy_extract` so the tests can hold the two to each other, qubit by qubit.

**Pinning measurements without a certificate.**  A memory experiment's detectors are declared
on the source circuit's classical bits.  The k-th measurement of circuit qubit q in the program
is the k-th `measure q[..] -> c[b]` of q in the source -- a compiler may reorder operations on
different wires, never two measurements of one -- so bit b is that measurement's record.  The
qubit -> ion map comes from `prog.meta["qubit_map"]`, else the certificate's `map`, else the
convention that circuit qubit i is ion `q{i}`.  A certificate, when given, is only
cross-checked against this (`meta.op` -> `circuit_ops`), never believed.

**An appended readout** (`MemoryExperiment.readout == "appended"`): the source stops after its
last syndrome round and the experiment's own readout follows the program's last instruction, on
the ions that hold the data qubits, in the memory's basis.  It is not a step of the programme,
so the noise text's step channels (memory, crosstalk) do not reach it; what does is said by the
text (the readout flip, the dephasing of the wait before it).

A program that cannot be read as a circuit -- a non-Clifford angle, an ion that holds no circuit
qubit, a qubit measured a different number of times than the source measures it, a program the
replay cannot execute -- raises `ExtractionError`.  A program that CAN be read but computes the
wrong thing extracts fine; `qccd.qec.check.noiseless_check` is what reports it.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .experiments import MemoryExperiment
from .noise import NoiseModel, get as get_noise

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
    #: priced from.  `id` is None for the preparation, the appended readout and the detector
    #: declarations.  `origin[i]` names what wrote line i: "translation", a channel of the
    #: noise text, or "detectors".
    lines: list[str] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)
    origin: list[str] = field(default_factory=list)
    #: the noise text that wrote the circuit: its name and digest
    evaluation: dict[str, str] = field(default_factory=dict)


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


# ------------------------------------------------------------------ why each line is there


def _explainer(nm: NoiseModel):
    """The `why` line for one written channel, in the words the Noise panels have always used."""
    rate = getattr(nm, "memory_rate_per_s", None)

    def explain(ch, p: float, targets, env: dict, ev) -> str | None:
        name = ch.name
        n = len(targets)
        if name == "ms":
            g = env["g"]
            a, b = g.ions[0], g.ions[1]
            nbar = max(float(g.step.nbar0.get(a, 0.0)), float(g.step.nbar0.get(b, 0.0)))
            chain = int(g.step.occ0.get(g.site, 2) or 2)
            return f"MS {a},{b}: n-bar {nbar:.3g}, chain of {chain} -> p = {p:.3g}"
        if name == "gate_1q":
            return f"one-qubit gate: p = {p:.3g} each (1 - fidelity)"
        if name in ("readout_z", "readout_x"):
            return f"readout: flip with p = {p:.3g} before each measurement (1 - fidelity)"
        if name == "reset":
            return f"reset: left in |1> with p = {p:.3g}"
        if name == "prepare":
            return f"every ion starts in |0>, prepared with the reset's error p = {p:.3g}"
        if name == "crosstalk":
            return f"measure in a shared trap: {n} other ion(s) disturbed with p = {p:.3g} each"
        if name in ("crosstalk_global", "crosstalk_reset"):
            k = env.get("k")
            kind = getattr(k, "kind", "measure")
            return f"{kind}: the {n} ion(s) in other traps are disturbed with p = {p:.3g} each"
        if name == "memory":
            k = env.get("k")
            dt = getattr(k, "duration", 0.0)
            return (f"takes {dt:.4g} us: the {n} ion(s) not operated on lose memory, p = {p:.3g} "
                    f"each (r = {rate}/s)")
        if name == "memory_coherent":
            i, o = env.get("i"), env.get("o")
            w = ev.term_call("waited", i, o) if (i is not None and o is not None) else 0.0
            return f"{i} waited {w:.4g} us since it was last operated on: coherent dephasing, p = {p:.3g}"
        if name == "idle":
            k = env.get("k")
            dt = getattr(k, "duration", 0.0)
            return f"takes {dt:.4g} us: every ion dephases with p = {p:.3g} ((1 - exp(-dt/T2)) / 2)"
        return f"{ch.title}: {ch.op}({p:.3g}) on {n} ion(s)"

    return explain


# ------------------------------------------------------------------ extraction


def extract(prog, arch, exp_or_spec, qasm: str | None = None, *,
            noise: NoiseModel | str | None = None, qubit_map=None,
            cert: Mapping | None = None, table: str | None = None) -> Extraction:
    """TSIR + architecture + experiment -> `Extraction` (noisy circuit, budget, stats).

    `exp_or_spec` is a `MemoryExperiment` or its `spec()` document; `qasm` overrides (or,
    for a spec, supplies) the source circuit.  `noise` is a model id or a `NoiseModel` whose
    knobs are set on its text (`qccd.qec.noise.evaluation`); `table` overrides its
    operating-point table.
    """
    from ..evalset.interp import EvalError, emit_circuit
    from ..evalset.words import Absent, WordError
    from .native import NotClifford
    from .noise import evaluation as noise_evaluation

    prog = load_program(prog)
    arch = load_arch(arch)
    if isinstance(exp_or_spec, MemoryExperiment):
        exp = exp_or_spec
    else:
        exp = MemoryExperiment.from_spec(exp_or_spec, qasm or "")
    text = qasm if qasm is not None else exp.qasm
    if not text:
        raise ExtractionError("no source circuit: pass the experiment's QASM")

    nm = get_noise(noise) if isinstance(noise, str) else (noise or get_noise("qccd-noise@1"))
    params = nm.resolve(arch)
    ev = noise_evaluation(nm)
    if table:
        ev = ev.with_clock(table)
        params["table"] = table

    # -- one replay: the run the text is read over
    # -- one replay: the run the text is read over, and the rule verdicts the evaluator would
    # give on the same clock (R4, R7c, R9, R11 ...)
    try:
        run = ev.run(prog, arch, experiment=exp, qasm=text, qubit_map=qubit_map, cert=cert, rules=True)
    except ExtractionError:
        raise
    except Exception as exc:  # ReplayError, and whatever a malformed program trips over
        raise ExtractionError(f"the program does not replay on {arch.name}: "
                              f"{type(exc).__name__}: {exc}") from exc
    report = run.facts["verification"]

    src = run.facts["source"]
    if any(q not in run.qubit_map for q in range(src.n_qubits)):
        missing = sorted(q for q in range(src.n_qubits) if q not in run.qubit_map)
        raise ExtractionError(f"the qubit map ({run.facts.get('qubit_map_origin')}) places no ion for "
                              f"circuit qubit(s) {missing[:8]}")
    unplaced = sorted({ion for ion in run.qubit_map.values() if ion not in run.ions})
    if unplaced:
        raise ExtractionError(f"the qubit map ({run.facts.get('qubit_map_origin')}) names ion(s) "
                              f"{unplaced[:8]} that the program never places")

    evaluator = ev.evaluator(run)
    try:
        circ = emit_circuit(evaluator, ev.channels, explain=_explainer(nm))
    except NotClifford as exc:
        raise ExtractionError(str(exc)) from exc
    except (EvalError, WordError, Absent) as exc:
        raise ExtractionError(str(exc)) from exc

    # -- the budget, under the model's channel names (the text's `counted as` lines)
    budget = {k: 0.0 for k in nm.channel_names()}
    for k, v in circ.budget.items():
        budget[k] = budget.get(k, 0.0) + v

    # -- the certificate, cross-checked against the measurement order, never believed
    disagreements: list[str] = []
    ordinal_of_op: dict[int, tuple[int, int]] = {}
    if cert is not None and cert.get("circuit_ops"):
        seen_q: dict[int, int] = defaultdict(int)
        for o in cert["circuit_ops"]:
            if o.get("name") == "measure" and o.get("qubits"):
                q = int(o["qubits"][0])
                ordinal_of_op[int(o["i"])] = (q, seen_q[q])
                seen_q[q] += 1
    qubit_of_ion = {ion: q for q, ion in run.qubit_map.items()}
    count: dict[int, int] = defaultdict(int)
    for st in run.steps:
        if st.kind != "measure":
            continue
        pinned = set()
        for o in st.ops:
            q = qubit_of_ion.get(o.ions[0])
            if q is None:
                continue
            pinned.add((q, count[q]))
            count[q] += 1
        if ordinal_of_op:
            claimed = {ordinal_of_op[int(x)] for x in (st.instr.meta or {}).get("op", ())
                       if int(x) in ordinal_of_op}
            if claimed != pinned:
                disagreements.append(f"instruction {st.instr.id}: certificate says "
                                     f"{sorted(claimed)}, order says {sorted(pinned)}")

    # -- the trace, in the shape the Noise panels read
    trace: list[dict[str, Any]] = []
    for tr in circ.trace:
        st = tr["step"]
        why = []
        for w in tr["why"]:
            if not why or why[-1] != w or not w.startswith("one-qubit"):
                why.append(w)
        if st is None:
            trace.append({"id": None, "type": "detectors", "a": tr["a"], "b": tr["b"], "dt_us": 0.0,
                          "why": ["the experiment's detectors and logical observable, over the "
                                  "measurement records"]})
        elif st.kind == "prepare":
            trace.append({"id": None, "type": "prepare", "a": tr["a"], "b": tr["b"], "dt_us": 0.0,
                          "why": why or ["every ion starts in |0>"]})
        elif st.kind == "readout":
            trace.append({"id": None, "type": "readout", "a": tr["a"], "b": tr["b"], "dt_us": 0.0,
                          "why": ["the experiment reads the data out after the last instruction, in "
                                  "the memory's basis, with the readout error"] + why})
        else:
            trace.append({"id": st.instr.id, "type": st.kind, "a": tr["a"], "b": tr["b"],
                          "dt_us": float(st.duration), "why": why})

    ops = run.ops
    ms = [o for o in ops if o.kind == "gate2"]
    nbars = [max(float(o.step.nbar0.get(a, 0.0)) for a in o.ions) for o in ms]
    chains = [int(o.step.occ0.get(o.site, 2) or 2) for o in ms]
    n_1q = sum(1 for o in ops if o.kind == "gate1" and o.gate not in ("VZ", "I"))
    readout = run.readout.ops if run.readout is not None else []
    import stim

    circuit = stim.Circuit("\n".join(circ.lines))
    stats = {
        "n_ms": len(ms),
        "n_1q": n_1q,
        "n_vz": sum(1 for o in ops if o.kind == "gate1" and o.gate == "VZ"),
        "n_measure": sum(1 for o in ops if o.kind == "measure" and o.step.in_programme),
        "n_reset": sum(1 for o in ops if o.kind == "reset"),
        "n_cool": sum(1 for st in run.steps if st.kind == "cool"),
        "n_readout_appended": len(readout),
        "n_instructions": len(prog.instructions),
        "nbar_max": max(nbars, default=0.0),
        "nbar_mean_at_ms": (sum(nbars) / len(nbars)) if nbars else 0.0,
        "chain_max": max(chains, default=0),
        "t_us": float(run.result.total_us),
        "t_readout_us": float(run.readout.duration) if run.readout is not None and readout else 0.0,
        "ions": len(circ.qubit_of_ion),
        "qubits": src.n_qubits,
        "detectors": circuit.num_detectors,
        "observables": circuit.num_observables,
        "qubit_map": run.facts.get("qubit_map_origin", "identity"),
        "rules_failed": sorted(report.rules.failed()),
        "rule_violations": report.rules.by_rule(),
        "cert_crosscheck": ("not provided" if not ordinal_of_op
                            else ("agrees" if not disagreements
                                  else "disagrees: " + "; ".join(disagreements[:4]))),
    }
    return Extraction(circuit=circuit, budget=budget, stats=stats, params=params,
                      qubit_map=dict(run.qubit_map), lines=list(circ.lines), trace=trace,
                      origin=list(circ.origin), evaluation={"name": ev.name, "digest": ev.digest()})
