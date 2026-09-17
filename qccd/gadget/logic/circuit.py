"""The circuit a gadget op executes.

A TSIR program interleaves transport (simd), cooling and gates.  Only `gate`, `measure`
and `reset` act on a qubit's state, so the circuit is those instructions in program order,
on the ions they name.  Ions keep their identity through every move, which is what makes
"read the circuit off the hardware program" sound: nothing a shuttle does can change which
qubit a later gate touches.  Parallel operations inside one instruction act on distinct
ions and commute, so writing them in list order loses nothing.

Measurement is in the Z basis and reset prepares |0>; an X-basis readout is an explicit H
before the measurement, as the hardware does it.  Every measurement appends one record,
in order; the records are what flows and detectors refer to.

`MPP` (a noiseless Pauli-product measurement) and `noisy=False` operations never come from
hardware: the reference experiments use them for the ideal encoders and decoders around a
gadget.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["COp", "Circuit", "NotACircuit", "from_tsir", "GATE1", "GATE2", "CLIFFORD1"]

#: single-qubit gates the logic layer understands, by canonical name
GATE1 = ("I", "X", "Y", "Z", "H", "S", "S_DAG", "T", "T_DAG")
CLIFFORD1 = ("I", "X", "Y", "Z", "H", "S", "S_DAG")
GATE2 = ("CX", "CZ")

_ALIASES = {"CNOT": "CX", "SDG": "S_DAG", "SDAG": "S_DAG", "TDG": "T_DAG", "TDAG": "T_DAG",
            "ID": "I", "IDLE": "I"}


class NotACircuit(ValueError):
    """The program names an operation the logic layer cannot interpret."""


def canonical(name: str) -> str:
    key = str(name).strip().upper()
    key = _ALIASES.get(key, key)
    if key in GATE1 or key in GATE2:
        return key
    raise NotACircuit(f"gate {name!r} has no circuit meaning here (known: "
                      f"{', '.join(GATE1 + GATE2)})")


@dataclass
class COp:
    """One circuit operation.  `targets` are qubit indices; a two-qubit gate's targets are
    flattened pairs `(c0, t0, c1, t1, ...)`.  `MPP` carries its product in `pauli`."""

    name: str
    targets: tuple[int, ...]
    source: int | None = None
    noisy: bool = True
    pauli: tuple[tuple[int, str], ...] = ()
    meta: dict = field(default_factory=dict)


class Circuit:
    """Operations over named qubits, with a measurement record."""

    def __init__(self, qubits=()):
        self.qubits: list[str] = []
        self.index: dict[str, int] = {}
        self.ops: list[COp] = []
        #: per record: (op index, qubit index or -1 for an MPP)
        self.records: list[tuple[int, int]] = []
        #: per record: whatever the producer attached (instruction meta, a label)
        self.labels: list[dict] = []
        for q in qubits:
            self.q(q)

    # -- building ----------------------------------------------------------------------

    def q(self, name: str) -> int:
        i = self.index.get(name)
        if i is None:
            i = self.index[name] = len(self.qubits)
            self.qubits.append(name)
        return i

    def add(self, name: str, targets, *, source=None, noisy=True, labels=None,
            meta=None) -> "Circuit":
        name = name if name in ("R", "M", "MPP") else canonical(name)
        idx = tuple(self.q(t) if isinstance(t, str) else int(t) for t in targets)
        if name in GATE2 and len(idx) % 2:
            raise NotACircuit(f"{name} needs pairs, got {len(idx)} targets")
        op = COp(name, idx, source=source, noisy=noisy, meta=dict(meta or {}))
        k = len(self.ops)
        self.ops.append(op)
        if name == "M":
            for j, q in enumerate(idx):
                self.records.append((k, q))
                lab = dict(meta or {})
                if labels is not None:
                    lab.update(labels[j] if isinstance(labels, (list, tuple)) else labels)
                lab.setdefault("qubit", self.qubits[q])
                self.labels.append(lab)
        return self

    def mpp(self, pauli: dict, *, label=None, noisy=False) -> int:
        """Measure one Pauli product (noiseless unless said otherwise); returns its record."""
        prod = tuple((self.q(q) if isinstance(q, str) else int(q), p.upper())
                     for q, p in pauli.items() if p.upper() != "I")
        k = len(self.ops)
        self.ops.append(COp("MPP", tuple(q for q, _ in prod), noisy=noisy, pauli=prod))
        self.records.append((k, -1))
        self.labels.append(dict(label or {}))
        return len(self.records) - 1

    def extend(self, other: "Circuit", *, noisy: bool | None = None, rename=None) -> int:
        """Append `other`, qubits matched by name (after `rename`); returns the record offset."""
        offset = len(self.records)
        remap = []
        for name in other.qubits:
            remap.append(self.q((rename or {}).get(name, name)))
        for op in other.ops:
            self.ops.append(COp(op.name, tuple(remap[t] for t in op.targets), source=op.source,
                                noisy=op.noisy if noisy is None else noisy,
                                pauli=tuple((remap[q], p) for q, p in op.pauli),
                                meta=dict(op.meta)))
        base = len(self.ops) - len(other.ops)
        for (k, q), lab in zip(other.records, other.labels):
            self.records.append((base + k, remap[q] if q >= 0 else -1))
            self.labels.append(dict(lab))
        return offset

    # -- reading -----------------------------------------------------------------------

    @property
    def n(self) -> int:
        return len(self.qubits)

    def counts(self) -> dict:
        out: dict[str, int] = {}
        for op in self.ops:
            w = len(op.targets) // (2 if op.name in GATE2 else 1) if op.name != "MPP" else 1
            out[op.name] = out.get(op.name, 0) + w
        return out

    def is_clifford(self) -> bool:
        return not any(op.name in ("T", "T_DAG") for op in self.ops)

    def to_stim(self, *, noise: float = 0.0) -> str:
        """Stim text (for cross-checks).  `noise` > 0 adds the fault model of `faults`."""
        lines = []
        for op in self.ops:
            t = " ".join(str(x) for x in op.targets)
            if op.name == "MPP":
                lines.append("MPP " + "*".join(f"{p}{q}" for q, p in op.pauli))
                continue
            if op.name == "M":
                if noise and op.noisy:
                    lines.append(f"M({noise}) {t}")
                else:
                    lines.append(f"M {t}")
                continue
            if op.name == "I":
                continue
            lines.append(f"{op.name} {t}")
            if noise and op.noisy:
                if op.name in GATE2:
                    lines.append(f"DEPOLARIZE2({noise}) {t}")
                elif op.name == "R":
                    lines.append(f"X_ERROR({noise}) {t}")
                else:
                    lines.append(f"DEPOLARIZE1({noise}) {t}")
        return "\n".join(lines)


def from_tsir(prog, *, rename=None, qubits=()) -> Circuit:
    """The circuit of a TSIR program.  Every ion the program places is a qubit, in the
    order `qubits` lists them first, then placement order."""
    c = Circuit()
    rn = rename or {}
    for q in qubits:
        c.q(rn.get(q, q))
    for ins in prog.instructions:
        if ins.type == "init":
            for ion in ins.placement:
                c.q(rn.get(ion, ion))
        elif ins.type == "gate":
            meta = dict(ins.meta or {})
            if ins.arity == 1:
                c.add(ins.gate, [rn.get(i, i) for i in ins.ions], source=ins.id, meta=meta)
            elif ins.pairs:
                flat = [rn.get(i, i) for pair in ins.pairs for i in pair]
                c.add(ins.gate, flat, source=ins.id, meta=meta)
            elif len(ins.ions) == 2:
                c.add(ins.gate, [rn.get(i, i) for i in ins.ions], source=ins.id, meta=meta)
            elif len(ins.ions) == 1:
                c.add(ins.gate, [rn.get(ins.ions[0], ins.ions[0])], source=ins.id, meta=meta)
            else:
                raise NotACircuit(f"instruction {ins.id}: a gate on {len(ins.ions)} ions "
                                  f"without arity")
        elif ins.type == "measure":
            c.add("M", [rn.get(i, i) for i in ins.ions], source=ins.id,
                  meta=dict(ins.meta or {}))
        elif ins.type == "reset":
            c.add("R", [rn.get(i, i) for i in ins.ions], source=ins.id,
                  meta=dict(ins.meta or {}))
    return c
