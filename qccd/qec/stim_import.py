"""A memory experiment from a stim circuit: someone else's syndrome circuit, exactly as written.

A code's syndrome-extraction circuit usually arrives as a stim program -- the form a QEC
group writes, simulates and publishes.  `from_stim` reads one into a `MemoryExperiment`:
the same gates in the same order (the order of the CNOTs is part of the experiment, since it
decides the circuit distance), the detectors and observables moved from stim's measurement
record onto the QASM circuit's classical bits, and the final transversal readout of the data
handed to the grader (`readout="appended"`, see `qccd.qec.experiments`).

The translation, instruction by instruction:

* `R q` -> nothing while q is untouched (every ion starts in |0>), else `reset q`;
  `RX q` -> `h q` while untouched, else `reset q; h q`;
* `H`, `X`, `Y`, `Z`, `S`, `S_DAG`, `CX`, `CZ`, `SWAP` -> the QASM gate;
* `M q` -> `measure q -> c[k]`; `MX q` -> `h q; measure q -> c[k]`;
  `MR q` -> `measure; reset`; `MRX q` -> `h; measure; reset; h`;
* `DETECTOR rec[-j]` -> the bit of the measurement j back; `OBSERVABLE_INCLUDE(i)` likewise;
* `TICK`, `QUBIT_COORDS`, `SHIFT_COORDS` and noise channels carry no gate and are dropped.

The data are the qubits of the LAST measurement layer -- the instructions after the last
gate that measure qubits measured nowhere else afterwards -- read in one basis per qubit.
They become q[0..n_data-1] in increasing stim index, the other qubits the ancillas after them,
so a QASM register is exactly the ions the circuit uses even when the stim indices have gaps.

`check_equivalent` is the proof that nothing changed: under the same depolarizing channel
after every two-qubit gate, the source and `ideal_stim` of the import have the same detector
error model, error for error.  stim is imported inside the functions, as everywhere in
`qccd.qec`.
"""

from __future__ import annotations

from .experiments import MemoryExperiment

__all__ = ["StimImportError", "from_stim", "check_equivalent"]

_GATES = {"H": "h", "X": "x", "Y": "y", "Z": "z", "S": "s", "S_DAG": "sdg",
          "CX": "cx", "CNOT": "cx", "CZ": "cz", "SWAP": "swap", "I": "id"}
_TWO = {"CX", "CNOT", "CZ", "SWAP"}
_MEAS = {"M": "Z", "MZ": "Z", "MX": "X", "MR": "Z", "MRZ": "Z", "MRX": "X"}
_RESET = {"R": "Z", "RZ": "Z", "RX": "X"}
_SKIP = {"TICK", "QUBIT_COORDS", "SHIFT_COORDS", "DEPOLARIZE1", "DEPOLARIZE2", "X_ERROR",
         "Y_ERROR", "Z_ERROR", "PAULI_CHANNEL_1", "PAULI_CHANNEL_2"}


class StimImportError(ValueError):
    """The stim circuit uses something a memory experiment cannot say."""


def _flat(circuit) -> list[tuple[str, list[int], list]]:
    out = []
    for ins in circuit.flattened():
        ts = ins.targets_copy()
        if ins.name in ("DETECTOR", "OBSERVABLE_INCLUDE"):
            if any(not t.is_measurement_record_target for t in ts):
                raise StimImportError(f"{ins.name} names a target that is not rec[-k]: {ins}")
            out.append((ins.name, [t.value for t in ts], ins.gate_args_copy()))
            continue
        if any(t.is_inverted_result_target for t in ts):
            raise StimImportError(f"inverted measurement targets are not supported: {ins}")
        out.append((ins.name, [t.value for t in ts if t.is_qubit_target], ins.gate_args_copy()))
    return out


def _final_readout(ops) -> tuple[set[tuple[int, int]], dict[int, str]]:
    """({(op index, qubit)} of the final readout, {data qubit: basis}).

    The readout is the measurements after the last gate of qubits measured nowhere before:
    an ancilla's last measurement can also come after the last gate (an X half that ends a
    round), but an ancilla has been measured in an earlier round or half-round already.  So a
    one-round circuit whose ancillas are measured once, at the very end, cannot be read."""
    last_gate = max((i for i, (n, _, _) in enumerate(ops)
                     if n in _GATES or n in _RESET or n in ("MR", "MRZ", "MRX")), default=-1)
    before = {q for n, qs, _ in ops[:last_gate + 1] if n in _MEAS for q in qs}
    data: dict[int, str] = {}
    final: set[tuple[int, int]] = set()
    for i in range(last_gate + 1, len(ops)):
        n, qs, _ = ops[i]
        if n in ("M", "MZ", "MX"):
            for q in qs:
                if q in before:
                    continue
                if q in data:
                    raise StimImportError(f"qubit {q} is read twice in the final readout")
                data[q] = _MEAS[n]
                final.add((i, q))
    if not data:
        raise StimImportError("no final readout: the circuit must end by measuring its data")
    return final, data


def from_stim(circuit, *, name: str, code: str, distance: int, rounds: int,
              basis: str | None = None) -> MemoryExperiment:
    """Read a stim memory circuit (`stim.Circuit` or its text) into a `MemoryExperiment`.

    `rounds` is the syndrome rounds the circuit runs (what a rate per round divides by); it is
    stated, not inferred, because an ancilla reused within a round is measured more than once.
    """
    import stim

    c = circuit if isinstance(circuit, stim.Circuit) else stim.Circuit(str(circuit))
    ops = _flat(c)
    for n, _, _ in ops:
        if not (n in _GATES or n in _MEAS or n in _RESET or n in _SKIP
                or n in ("DETECTOR", "OBSERVABLE_INCLUDE")):
            raise StimImportError(f"stim instruction {n} has no QASM translation here")
    final, data_basis = _final_readout(ops)
    used = sorted({q for n, qs, _ in ops if n not in ("DETECTOR", "OBSERVABLE_INCLUDE") for q in qs})
    data = sorted(data_basis)
    anc = [q for q in used if q not in data_basis]
    to = {q: i for i, q in enumerate(data + anc)}
    n_data, n_q = len(data), len(used)

    lines: list[str] = []
    touched: set[int] = set()
    bit_of_meas: list[int] = []       # stim measurement index -> classical bit
    n_bits = 0
    readout_bits: list[tuple[int, int]] = []
    detectors: list[tuple[int, ...]] = []
    observables: dict[int, list[int]] = {}
    n_meas = 0

    def q_(q: int) -> str:
        return f"q[{to[q]}]"

    for i, (n, qs, args) in enumerate(ops):
        if n in _SKIP:
            continue
        if n == "DETECTOR":
            detectors.append(tuple(sorted(bit_of_meas[n_meas + k] for k in qs)))
            continue
        if n == "OBSERVABLE_INCLUDE":
            observables.setdefault(int(args[0]) if args else 0, []).extend(
                bit_of_meas[n_meas + k] for k in qs)
            continue
        if n in _RESET:
            for q in qs:
                if q in touched:
                    lines.append(f"reset {q_(q)};")
                if _RESET[n] == "X":
                    lines.append(f"h {q_(q)};")
                touched.add(q)
            continue
        if n in _GATES:
            if n in _TWO:
                for a, b in zip(qs[::2], qs[1::2]):
                    lines.append(f"{_GATES[n]} {q_(a)},{q_(b)};")
            else:
                lines.extend(f"{_GATES[n]} {q_(q)};" for q in qs)
            touched.update(qs)
            continue
        # a measurement: the data's final readout (the grader appends it; the bit is
        # reserved), or an ancilla's (possibly with a reset)
        for q in qs:
            if (i, q) in final:
                bit_of_meas.append(n_bits)
                readout_bits.append((to[q], n_bits))
                n_bits += 1
                n_meas += 1
                continue
            x = _MEAS[n] == "X"
            if x:
                lines.append(f"h {q_(q)};")
            lines.append(f"measure {q_(q)} -> c[{n_bits}];")
            bit_of_meas.append(n_bits)
            n_bits += 1
            n_meas += 1
            if n.startswith("MR"):
                lines.append(f"reset {q_(q)};")
                if x:
                    lines.append(f"h {q_(q)};")
            touched.add(q)

    bases = {data_basis[q] for q in data}
    label = "".join(sorted(bases))
    if basis is not None and basis != label:
        raise StimImportError(f"the data are read in {label}, not {basis}")
    obs = tuple(tuple(sorted(set(b for b in bits if bits.count(b) % 2)))
                for _, bits in sorted(observables.items()))
    # a detector naming one bit twice cancels it: parity is what the grader checks
    dets = tuple(tuple(sorted(b for b in set(d) if d.count(b) % 2)) for d in detectors)
    D = n_bits - n_data
    head = ['OPENQASM 2.0;', 'include "qelib1.inc";',
            f"// {name}: {code} memory in the {label} basis, read from a stim circuit; "
            f"data q[0..{n_data - 1}], ancillas q[{n_data}..{n_q - 1}]; "
            f"data readout c[{D}..{n_bits - 1}] appended by the grader",
            f"qreg q[{n_q}];", f"creg c[{n_bits}];"]
    return MemoryExperiment(
        name=name, code=code, distance=distance, rounds=rounds, basis=label,
        n_data=n_data, n_ancilla=n_q - n_data, qasm="\n".join(head + lines) + "\n",
        detectors=dets, observables=obs, readout="appended",
        readout_bits=tuple(readout_bits),
        readout_basis=(tuple(data_basis[data[q]] for q, _ in readout_bits) if len(bases) > 1 else ()))


def _cx_noise(circuit, p: float):
    """`circuit` with DEPOLARIZE2(p) after every two-qubit gate and nothing else noisy.

    Pair by pair: stim fuses consecutive `CX` lines into one instruction, so noise placed
    after an instruction could land after several layers."""
    import stim

    out = stim.Circuit()
    for ins in circuit.flattened():
        if ins.name in _TWO:
            t = [x.value for x in ins.targets_copy()]
            for a, b in zip(t[::2], t[1::2]):
                out.append(ins.name, [a, b])
                out.append("DEPOLARIZE2", [a, b], p)
        else:
            out.append(ins)
    return out


def check_equivalent(source, exp: MemoryExperiment, p: float = 1e-3) -> list[str]:
    """Differences between `source` and the imported experiment (empty: none).

    Both get DEPOLARIZE2(p) after every two-qubit gate; their detector error models must
    then be equal -- the same detectors flipped by the same faults with the same
    probabilities -- and both must be deterministic without noise.
    """
    import stim

    from .experiments import ideal_stim

    src = source if isinstance(source, stim.Circuit) else stim.Circuit(str(source))
    mine = ideal_stim(exp, 0.0)
    out: list[str] = []
    if src.num_detectors != mine.num_detectors:
        out.append(f"detectors: {src.num_detectors} in the source, {mine.num_detectors} imported")
    if src.num_observables != mine.num_observables:
        out.append(f"observables: {src.num_observables} vs {mine.num_observables}")
    try:
        a = _errors(_cx_noise(src, p).detector_error_model(allow_gauge_detectors=False))
        b = _errors(_cx_noise(mine, p).detector_error_model(allow_gauge_detectors=False))
    except ValueError as exc:
        return out + [f"not deterministic: {str(exc).splitlines()[0][:200]}"]
    if a.keys() != b.keys():
        out.append(f"fault mechanisms differ: {len(a.keys() - b.keys())} only in the source, "
                   f"{len(b.keys() - a.keys())} only in the import")
    elif any(abs(a[k] - b[k]) > 1e-12 for k in a):
        out.append("the same fault mechanisms with different probabilities")
    return out


def _errors(dem) -> dict[tuple, float]:
    """{(detectors and observables flipped): probability}, the model without its annotations
    (detector coordinates are the source's own and carry no physics)."""
    out: dict[tuple, float] = {}
    for inst in dem.flattened():
        if inst.type != "error":
            continue
        key = tuple(sorted(str(t) for t in inst.targets_copy() if not t.is_separator()))
        p = inst.args_copy()[0]
        q = out.get(key, 0.0)
        out[key] = q + p - 2 * q * p      # two independent mechanisms with one effect
    return out
