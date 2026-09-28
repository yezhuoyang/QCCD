"""Memory experiments: a QASM circuit plus detectors written on its classical bits.

A memory experiment prepares a code block, measures its checks for `rounds` rounds, reads
the data out transversally and asks whether a logical operator survived.  Everything a
grader needs is stated on the SOURCE circuit's classical bits -- `detectors` and
`observables` are lists of bits whose parity is 0 when nothing went wrong -- so the same
experiment grades any compiler's output: the bits are pinned to a hardware program by
measurement order (`qccd.qec.qasm`), never by what the compiler says it did.

The construction (Z basis; X basis swaps the roles of X and Z):

* data start in |0>; one fresh ancilla per check, reset between rounds;
* an X check is `h a; cx a,d...; h a`, a Z check is `cx d,a...`; every ancilla is measured;
* detectors: each Z check alone in round 0 (|0>^n fixes it), every check against its own
  previous value from round 1 on (an X check is random in round 0 and repeats after),
  and each Z check's last value against the parity of its data in the final readout;
* observables: each Z logical's parity in the final readout.

**The schedule is part of the experiment**, because it decides the circuit distance.  The
rotated surface code uses `qccd.gadget.surface`'s four-layer Tomita–Svore order, which is
hook-safe and so keeps distance d.  Other CSS codes measure one check after another (all X
checks, then all Z checks), which is always valid; with `flags=True` each weight >= 4 check
of the type whose hook errors reach the observable gets one flag qubit (Chao–Reichardt),
which is what gives the Steane code circuit distance 3 -- without it, a hook X_aX_b plus one
more fault on the third point of their line of the Fano plane is a logical error from two
faults.

**Where the final data readout happens** is `readout`:

* `"program"` -- the QASM ends with the transversal `measure` of every data qubit, and the
  compiled program must perform it.
* `"appended"` (the default) -- the QASM stops after the last syndrome round; the classical
  register still holds the data bits, with the same numbering, so the detectors and
  observables are unchanged, and `readout_bits` says which bit each data qubit's readout
  writes.  The grader appends that readout itself, after the program's last instruction,
  on the ions that hold the data, with the device's own measurement error
  (`qccd.qec.extract`).  The reason is the reference compiler: qccdc's rigid rotation --
  the only mode whose output passes every rule on a loop device -- refuses a `measure` on
  an ion that rides the loop, and data ions ride it.  The syndrome rounds alone compile by
  rotation and pass.  It is an approximation to revisit: the readout's transport, its
  duration beyond one measurement and its effect on the schedule are not charged.

`ideal_stim` turns an experiment into the stim circuit of its source (plus the appended
readout), with optional uniform circuit-level noise: the reference an extracted hardware
circuit is compared with, and the thing whose circuit distance the tests measure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from ..gadget import gf2
from ..gadget.codes import TUTORIAL_BB, CSSCode, bivariate_bicycle, parse_poly
from ..gadget.surface import patch
from .qasm import GATES_1Q, GATES_2Q, parse_qasm
from .stimtext import StimText, declare

__all__ = [
    "MemoryExperiment",
    "SPEC_KIND",
    "repetition",
    "rotated_surface",
    "steane",
    "steane_code",
    "css_memory",
    "bb",
    "EXPERIMENTS",
    "READOUTS",
    "get",
    "ideal_stim",
    "append_readout",
]

SPEC_KIND = "qccd.detectors"
READOUTS = ("appended", "program")


@dataclass(frozen=True)
class MemoryExperiment:
    """A memory experiment: its circuit and the parities that must come out 0."""

    name: str
    code: str
    distance: int
    rounds: int
    basis: str
    n_data: int
    n_ancilla: int
    qasm: str
    detectors: tuple[tuple[int, ...], ...]
    observables: tuple[tuple[int, ...], ...]
    #: "program" or "appended" (see the module docstring)
    readout: str = "program"
    #: with an appended readout: (data qubit, the classical bit its readout writes)
    readout_bits: tuple[tuple[int, int], ...] = ()

    def spec(self) -> dict:
        """The detector declaration, JSON-safe; with the QASM it is the whole experiment."""
        return {
            "kind": SPEC_KIND,
            "version": 1,
            "experiment": self.name,
            "code": self.code,
            "distance": self.distance,
            "rounds": self.rounds,
            "basis": self.basis,
            "n_data": self.n_data,
            "n_ancilla": self.n_ancilla,
            "detectors": [list(d) for d in self.detectors],
            "observables": [list(o) for o in self.observables],
            "readout": self.readout,
            **({"readout_bits": {str(q): b for q, b in self.readout_bits}}
               if self.readout == "appended" else {}),
        }

    @classmethod
    def from_spec(cls, spec: Mapping, qasm: str = "") -> "MemoryExperiment":
        if spec.get("kind") != SPEC_KIND or int(spec.get("version", 0)) != 1:
            raise ValueError(f"not a {SPEC_KIND} v1 document: kind={spec.get('kind')!r} "
                             f"version={spec.get('version')!r}")
        n_data = spec.get("n_data")
        n_anc = spec.get("n_ancilla")
        if (n_data is None or n_anc is None) and qasm:
            n_q = parse_qasm(qasm).n_qubits
            n_data = n_q if n_data is None else n_data
            n_anc = n_q - int(n_data) if n_anc is None else n_anc
        readout = str(spec.get("readout", "program"))
        if readout not in READOUTS:
            raise ValueError(f"readout must be one of {READOUTS}, not {readout!r}")
        bits = spec.get("readout_bits") or {}
        if readout == "appended" and not bits:
            raise ValueError("an appended readout needs `readout_bits`")
        return cls(
            name=str(spec["experiment"]),
            code=str(spec.get("code", "")),
            distance=int(spec.get("distance", 0)),
            rounds=int(spec.get("rounds", 1)),
            basis=str(spec.get("basis", "Z")),
            n_data=int(n_data or 0),
            n_ancilla=int(n_anc or 0),
            qasm=qasm,
            detectors=tuple(tuple(int(b) for b in d) for d in spec["detectors"]),
            observables=tuple(tuple(int(b) for b in o) for o in spec["observables"]),
            readout=readout,
            readout_bits=tuple(sorted((int(q), int(b)) for q, b in bits.items())),
        )

    @property
    def matchable(self) -> bool:
        """Can minimum-weight matching decode this experiment at full distance?

        Only when every measured bit sits in at most two detectors -- i.e. every data
        qubit is in at most two checks of the type that sees it (repetition, surface).
        A Steane or BB qubit in three checks gives a single fault three detection events;
        stim can still split that into two graph edges, and matching then treats one fault
        as two and mis-corrects it (the flagged Steane circuit decodes ~4x worse under
        matching than under BP-OSD).  That is invisible in the circuit's error model, so
        it is read off the experiment, which knows its checks.
        """
        uses: dict[int, int] = {}
        for det in self.detectors:
            for b in det:
                uses[b] = uses.get(b, 0) + 1
        return max(uses.values(), default=0) <= 2

    def summary(self) -> dict:
        """What a report names the experiment by: the spec without its bit lists."""
        return {"experiment": self.name, "code": self.code, "distance": self.distance,
                "rounds": self.rounds, "basis": self.basis, "n_data": self.n_data,
                "n_ancilla": self.n_ancilla, "detectors": len(self.detectors),
                "observables": len(self.observables), "matchable": self.matchable,
                "readout": self.readout}


# ------------------------------------------------------------------ the builder


@dataclass(frozen=True)
class _Check:
    basis: str                          # "X" | "Z"
    support: tuple[int, ...]            # data qubits, in contact order
    layers: tuple[int, ...] | None = None


def _memory(name: str, code: str, distance: int, n_data: int, checks: Sequence[_Check],
            logicals: Sequence[Sequence[int]], rounds: int, basis: str, *,
            layered: bool, flags: bool = False, note: str = "",
            readout: str = "appended") -> MemoryExperiment:
    """The one construction every generator shares (see the module docstring)."""
    if basis not in ("X", "Z"):
        raise ValueError(f"basis must be 'X' or 'Z', got {basis!r}")
    if readout not in READOUTS:
        raise ValueError(f"readout must be one of {READOUTS}, not {readout!r}")
    if rounds < 1:
        raise ValueError("a memory experiment needs at least one round")
    if layered and flags:
        raise ValueError("a layered schedule is hook-safe by construction; it takes no flags")
    other = "X" if basis == "Z" else "Z"
    m = len(checks)
    anc = [n_data + k for k in range(m)]
    # a hook on a check of the OTHER type spreads the error type that flips the observable
    flagged = [k for k, c in enumerate(checks)
               if flags and c.basis == other and len(c.support) >= 4]
    flag_of = {k: n_data + m + j for j, k in enumerate(flagged)}
    nf = len(flagged)
    per_round = m + nf
    n_q = n_data + m + nf
    D = rounds * per_round
    n_bits = D + n_data

    def bit(r: int, k: int) -> int:
        return r * per_round + k

    def fbit(r: int, j: int) -> int:
        return r * per_round + m + j

    out = ['OPENQASM 2.0;', 'include "qelib1.inc";',
           f"// {name}: {code} memory in the {basis} basis, {rounds} round(s); "
           f"data q[0..{n_data - 1}], ancillas q[{n_data}..{n_q - 1}]" + (f"; {note}" if note else "")
           + (f"; data readout c[{D}..{n_bits - 1}] appended by the grader"
              if readout == "appended" else ""),
           f"qreg q[{n_q}];", f"creg c[{n_bits}];"]
    if basis == "X":
        out += [f"h q[{d}];" for d in range(n_data)]

    def contact(c: _Check, a: int, d: int) -> str:
        return f"cx q[{a}],q[{d}];" if c.basis == "X" else f"cx q[{d}],q[{a}];"

    for r in range(rounds):
        if r:
            out += [f"reset q[{a}];" for a in anc] + [f"reset q[{flag_of[k]}];" for k in flagged]
        if layered:
            out += [f"h q[{anc[k]}];" for k, c in enumerate(checks) if c.basis == "X"]
            steps = sorted({t for c in checks for t in (c.layers or range(len(c.support)))})
            for t in steps:
                for k, c in enumerate(checks):
                    lay = c.layers or tuple(range(len(c.support)))
                    for d, lt in zip(c.support, lay):
                        if lt == t:
                            out.append(contact(c, anc[k], d))
            out += [f"h q[{anc[k]}];" for k, c in enumerate(checks) if c.basis == "X"]
            out += [f"measure q[{anc[k]}] -> c[{bit(r, k)}];" for k in range(m)]
        else:
            for k, c in enumerate(checks):
                a = anc[k]
                f = flag_of.get(k)
                # the flag brackets every contact but the first and the last: an ancilla fault
                # between them would spread to two or more data qubits, and it flips the flag
                if f is not None and c.basis == "X":
                    bracket = f"cx q[{a}],q[{f}];"         # flag |0>: catches X on the ancilla
                elif f is not None:
                    bracket = f"cx q[{f}],q[{a}];"         # flag |+>: catches Z on the ancilla
                    out.append(f"h q[{f}];")
                if c.basis == "X":
                    out.append(f"h q[{a}];")
                for i, d in enumerate(c.support):
                    if f is not None and i in (1, len(c.support) - 1):
                        out.append(bracket)
                    out.append(contact(c, a, d))
                if c.basis == "X":
                    out.append(f"h q[{a}];")
                out.append(f"measure q[{a}] -> c[{bit(r, k)}];")
                if f is not None:
                    if c.basis == "Z":
                        out.append(f"h q[{f}];")
                    out.append(f"measure q[{f}] -> c[{fbit(r, flagged.index(k))}];")
    if readout == "program":
        if basis == "X":
            out += [f"h q[{d}];" for d in range(n_data)]
        out += [f"measure q[{d}] -> c[{D + d}];" for d in range(n_data)]

    dets: list[tuple[int, ...]] = []
    for r in range(rounds):
        for k, c in enumerate(checks):
            if r == 0:
                if c.basis == basis:
                    dets.append((bit(0, k),))
            else:
                dets.append((bit(r, k), bit(r - 1, k)))
        for j in range(nf):
            dets.append((fbit(r, j),))
    for k, c in enumerate(checks):
        if c.basis == basis:
            dets.append((bit(rounds - 1, k),) + tuple(D + d for d in sorted(c.support)))
    obs = tuple(tuple(D + d for d in sorted(lg)) for lg in logicals)
    return MemoryExperiment(name=name, code=code, distance=distance, rounds=rounds,
                            basis=basis, n_data=n_data, n_ancilla=m + nf,
                            qasm="\n".join(out) + "\n", detectors=tuple(dets),
                            observables=obs, readout=readout,
                            readout_bits=(tuple((d, D + d) for d in range(n_data))
                                          if readout == "appended" else ()))


# ------------------------------------------------------------------ generators


def repetition(d: int, rounds: int | None = None, *,
               readout: str = "appended") -> MemoryExperiment:
    """The bit-flip repetition code: Z checks Z_i Z_{i+1}, logical Z_0.

    Only a Z-basis memory makes sense: the code does not protect against phase flips.
    """
    if d < 2:
        raise ValueError("a repetition code needs d >= 2")
    r = d if rounds is None else rounds
    checks = [_Check("Z", (i, i + 1), (0, 1)) for i in range(d - 1)]
    return _memory(f"rep{d}_r{r}_z", f"repetition_d{d}", d, d, checks, [[0]], r, "Z",
                   layered=True, readout=readout)


def rotated_surface(d: int, rounds: int | None = None, basis: str = "Z", *,
                    readout: str = "appended") -> MemoryExperiment:
    """The rotated surface code of `qccd.gadget.surface.patch(d, d)`, in its hook-safe
    four-layer Tomita–Svore schedule: X checks NW NE SW SE, Z checks NW SW NE SE."""
    r = d if rounds is None else rounds
    p = patch(d, d, name=f"surface_d{d}")
    checks = [_Check(c.basis, tuple(c.support), tuple(c.layers))
              for c in sorted(p.checks, key=lambda c: (c.basis != "X", int(c.name[1:])))]
    return _memory(f"surface{d}_r{r}_{basis.lower()}", f"rotated_surface_d{d}", d, p.n,
                   checks, [p.logical(basis)], r, basis, layered=True, readout=readout)


def steane_code() -> CSSCode:
    """[[7,1,3]], with the check supports of the shipped `steane@1` board."""
    rows = tuple(sum(1 << q for q in s) for s in ((3, 4, 5, 6), (1, 2, 5, 6), (0, 2, 4, 6)))
    return CSSCode(name="steane", n=7, hx=rows, hz=rows, family="steane",
                   params={"declared": [7, 1, 3]})


def steane(rounds: int = 3, basis: str = "Z", *, flags: bool = True,
           readout: str = "appended") -> MemoryExperiment:
    """The Steane code, one flag per weight-4 check of the hook-prone type (see module).

    The flags are what give it circuit distance 3, and they are also why it does not
    compile by rigid rotation: qccdc's `rotate` refuses a flag ancilla that rides the
    loop.  It compiles with qccdc's router (`compile`) on grids and on cyclone_base.
    """
    return css_memory(steane_code(), rounds, f"steane_r{rounds}_{basis.lower()}", basis,
                      distance=3, flags=flags, readout=readout)


def css_memory(code: CSSCode, rounds: int, name: str | None = None, basis: str = "Z", *,
               distance: int | None = None, flags: bool = False,
               readout: str = "appended") -> MemoryExperiment:
    """Any CSS code, checks measured one after another (all X, then all Z).

    The observables are the code's logical basis (`CSSCode.logical_basis`, LogicQ's own
    representatives): the Z logicals for a Z-basis memory, the X logicals for X.  The
    distance is `distance`, else the code's declared one; it is what the experiment is
    named for, not something computed here.
    """
    if distance is None:
        declared = code.params.get("declared")
        if not declared:
            raise ValueError(f"{code.name}: pass `distance`; the code declares none")
        distance = int(declared[2])
    lx, lz = code.logical_basis
    checks = ([_Check("X", tuple(gf2.support(h))) for h in code.hx]
              + [_Check("Z", tuple(gf2.support(h))) for h in code.hz])
    logicals = [gf2.support(row) for row in (lz if basis == "Z" else lx)]
    return _memory(name or f"{code.name}_r{rounds}_{basis.lower()}", code.name, distance,
                   code.n, checks, logicals, rounds, basis, layered=False, flags=flags,
                   note="flagged" if flags else "", readout=readout)


def bb(name: str = "bb72", rounds: int | None = None, basis: str = "Z", *,
       readout: str = "appended") -> MemoryExperiment:
    """A bivariate-bicycle code of `TUTORIAL_BB` (Bravyi et al. 2024), measured naively.

    Sequential single-ancilla extraction of weight-6 checks is not distance-preserving;
    it is a valid, deterministic circuit and a hard routing problem, which is what a
    compiler benchmark needs from it.
    """
    l, m, a, b, (n, k, d) = TUTORIAL_BB[name]
    code = bivariate_bicycle(name, l, m, parse_poly(a, l, m), parse_poly(b, l, m),
                             declared=(n, k, d))
    r = d if rounds is None else rounds
    return css_memory(code, r, f"{name}_r{r}_{basis.lower()}", basis, distance=d,
                      readout=readout)


#: The named experiments boards and the benchmark suite refer to.  Keys are short; the
#: experiment's own `name` carries its rounds and basis.  Rounds are the distance except
#: for bb72: with qccdc as of 2026-09-28, two or more rounds of it are unroutable on every
#: shipped device (one round compiles on grid9x9 and deck_unit_cell in ~3 s).
EXPERIMENTS: dict[str, Callable[[], MemoryExperiment]] = {
    "rep3": lambda: repetition(3),
    "rep5": lambda: repetition(5),
    "rep9": lambda: repetition(9),
    "surface3": lambda: rotated_surface(3),
    "surface5": lambda: rotated_surface(5),
    "steane": lambda: steane(3),
    "bb18": lambda: bb("bb18"),
    "bb72": lambda: bb("bb72", rounds=1),
}


def get(key: str) -> MemoryExperiment:
    try:
        return EXPERIMENTS[key]()
    except KeyError:
        raise KeyError(f"no memory experiment {key!r} (known: {', '.join(EXPERIMENTS)})") from None


# ------------------------------------------------------------------ the source as stim


def ideal_stim(exp: MemoryExperiment, p: float = 0.0):
    """The SOURCE circuit as a `stim.Circuit`, with detectors and observables.

    `p > 0` adds uniform circuit-level noise: DEPOLARIZE2(p) after each two-qubit gate,
    DEPOLARIZE1(p) after each one-qubit gate, X_ERROR(p) before each measurement and after
    each reset.  No noise on the initial |0> (the hardware extraction adds none either).
    An appended readout is added after the source, as `extract` adds it: a flip of
    probability p, then a measurement in the memory's basis.
    """
    prog = parse_qasm(exp.qasm)
    c = StimText()
    c.append("R", range(prog.n_qubits))
    meas_of_bit: dict[int, int] = {}
    n_meas = 0
    for op in prog.ops:
        q = list(op.qubits)
        if op.name == "M":
            if p:
                c.append("X_ERROR", q, p)
            c.append("M", q)
            meas_of_bit[op.bit] = n_meas  # type: ignore[index]
            n_meas += 1
        elif op.name == "R":
            c.append("R", q)
            if p:
                c.append("X_ERROR", q, p)
        elif op.name in GATES_2Q.values():
            c.append(op.name, q)
            if p:
                c.append("DEPOLARIZE2", q, p)
        elif op.name in GATES_1Q.values():
            if op.name != "I":
                c.append(op.name, q)
            if p:
                c.append("DEPOLARIZE1", q, p)
    for q, b in exp.readout_bits:
        append_readout(c, exp.basis, [q], p)
        meas_of_bit[b] = n_meas
        n_meas += 1
    declare(c, exp.detectors, exp.observables, meas_of_bit, n_meas)
    return c.circuit()


def append_readout(c, basis: str, qubits: Sequence[int], p: float) -> None:
    """The appended data readout: a flip that the measurement sees, then the measurement,
    in the memory's basis (X_ERROR + M for Z; Z_ERROR + MX for X)."""
    flip, meas = ("X_ERROR", "M") if basis == "Z" else ("Z_ERROR", "MX")
    if p:
        c.append(flip, list(qubits), p)
    c.append(meas, list(qubits))
