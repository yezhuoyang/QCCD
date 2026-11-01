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

**Both bases at once.**  A Z-basis memory cannot see dephasing: a Z error flips no Z logical,
and dephasing is what a trapped-ion machine accrues while it idles and shuttles.  So
`rotated_surface_xz` runs two patches in lockstep in one circuit, one a Z memory and one an X
memory, with one observable each; a shot fails when either does (`decode.estimate_ler`).  Its
`basis` is "XZ" and its readout says each data qubit's basis (`readout_basis`).

**Surgeries** (`lattice_surgery`, `surgery_cnot`) are experiments too: patches on one lattice of
surface-code faces, in phases -- each a set of checks measured some rounds, after the data it
reads and the data it prepares.  Their detectors follow from that by one rule (`_phased`): a
check's first value in a phase is compared with its face's last value in the phase before when
the data it gained were just prepared in its basis and the data it lost were just read in it
(those readouts join the comparison); otherwise it is random.  A check of the phase before
whose data were all read in its basis is compared with those readouts.  `coords` says where
each data qubit sits on the lattice and `faces` which face each ancilla measures, which a
compiler that plays the checks by geometry reads (`qccd.compile.tiled_surface`).

**H̄** (`hadamard_bar`) is a surgery with two more kinds of step between phases: a transversal H
(each check changes type; a check of the phase before is then compared with the new one on its
support), and a move of every data state one lattice site through the measure ion of the face
between -- the checks move with it.  Its observable is the gate's flow, Z̄ -> X̄ or X̄ -> Z̄,
with the corrections stim's flow solver finds.

`ideal_stim` turns an experiment into the stim circuit of its source (plus the appended
readout), with optional uniform circuit-level noise: the reference an extracted hardware
circuit is compared with, and the thing whose circuit distance the tests measure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    "rotated_surface_xz",
    "logical_qubits",
    "css_memory_xz",
    "css_memory_seq",
    "shor_code",
    "repetition_code",
    "bb_code",
    "steane",
    "steane_code",
    "css_memory",
    "bb",
    "lattice_surgery",
    "surgery_cnot",
    "hadamard_bar",
    "corridor_merge",
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
    #: the basis each `readout_bits` entry is read in, when they differ ("XZ" experiments);
    #: empty means every data qubit is read in `basis`
    readout_basis: tuple[str, ...] = ()
    #: (row, column) of each data qubit on its surface-code lattice, for a surgery; empty
    #: otherwise.  Not part of the spec: the grader never needs it
    coords: tuple[tuple[int, int], ...] = ()
    #: the lattice face each ancilla (n_data + k) measures, for a surgery; empty otherwise.  Not
    #: part of the spec either
    faces: tuple[tuple[int, int], ...] = ()

    def readout_plan(self) -> list[tuple[int, int, str]]:
        """(data qubit, classical bit, basis) for each appended readout, in order."""
        bases = self.readout_basis or (self.basis,) * len(self.readout_bits)
        return [(q, b, s) for (q, b), s in zip(self.readout_bits, bases)]

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
            **({"readout_basis": {str(q): s for (q, _), s in zip(self.readout_bits,
                                                                  self.readout_basis)}}
               if self.readout == "appended" and self.readout_basis else {}),
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
        rb = sorted((int(q), int(b)) for q, b in bits.items())
        bases = spec.get("readout_basis") or {}
        if bases and (set(bases) != {str(q) for q, _ in rb}
                      or not set(bases.values()) <= {"X", "Z"}):
            raise ValueError("`readout_basis` must name X or Z for every data qubit of `readout_bits`")
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
            readout_bits=tuple(rb),
            readout_basis=tuple(str(bases[str(q)]) for q, _ in rb) if bases else (),
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

    @property
    def logical_qubits(self) -> int:
        """k, the logical qubits the experiment tests.  A two-basis ("XZ") experiment tests each
        one twice, a copy of the code per basis, so it is half its observables."""
        n = len(self.observables)
        return max(1, n // 2) if self.basis == "XZ" else max(1, n)

    def summary(self) -> dict:
        """What a report names the experiment by: the spec without its bit lists."""
        return {"experiment": self.name, "code": self.code, "distance": self.distance,
                "rounds": self.rounds, "basis": self.basis, "n_data": self.n_data,
                "n_ancilla": self.n_ancilla, "detectors": len(self.detectors),
                "observables": len(self.observables), "matchable": self.matchable,
                "readout": self.readout, "logical_qubits": self.logical_qubits,
                "ions": self.n_data + self.n_ancilla}


def logical_qubits(exp: MemoryExperiment) -> int:
    """k of an experiment (`MemoryExperiment.logical_qubits`): what Goal 1's leaderboard divides a
    logical error rate per round by, so that codes encoding more qubits are compared fairly."""
    return exp.logical_qubits


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
    """The one construction every generator shares (see the module docstring).

    `basis` is "X" or "Z", or one of them per data qubit (patches side by side); each check
    then belongs to the basis of its data, which must all be in one basis.
    """
    dbasis = [basis] * n_data if isinstance(basis, str) else [str(b) for b in basis]
    if len(dbasis) != n_data or not set(dbasis) <= {"X", "Z"}:
        raise ValueError(f"basis must be 'X' or 'Z' (or one per data qubit), got {basis!r}")
    mixed = len(set(dbasis)) > 1
    home: list[str] = []
    for c in checks:
        on = {dbasis[d] for d in c.support}
        if len(on) != 1:
            raise ValueError(f"a check spans data of both bases: {c.support}")
        home.append(on.pop())
    label = "".join(sorted(set(dbasis)))
    if readout not in READOUTS:
        raise ValueError(f"readout must be one of {READOUTS}, not {readout!r}")
    if rounds < 1:
        raise ValueError("a memory experiment needs at least one round")
    if layered and flags:
        raise ValueError("a layered schedule is hook-safe by construction; it takes no flags")
    m = len(checks)
    anc = [n_data + k for k in range(m)]
    # a hook on a check of the OTHER type (than its own patch's memory) spreads the error type
    # that flips that patch's observable
    flagged = [k for k, c in enumerate(checks)
               if flags and c.basis != home[k] and len(c.support) >= 4]
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
           f"// {name}: {code} memory in the {label} basis, {rounds} round(s); "
           f"data q[0..{n_data - 1}], ancillas q[{n_data}..{n_q - 1}]" + (f"; {note}" if note else "")
           + (f"; data readout c[{D}..{n_bits - 1}] appended by the grader"
              if readout == "appended" else ""),
           f"qreg q[{n_q}];", f"creg c[{n_bits}];"]
    out += [f"h q[{d}];" for d in range(n_data) if dbasis[d] == "X"]

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
        out += [f"h q[{d}];" for d in range(n_data) if dbasis[d] == "X"]
        out += [f"measure q[{d}] -> c[{D + d}];" for d in range(n_data)]

    dets: list[tuple[int, ...]] = []
    for r in range(rounds):
        for k, c in enumerate(checks):
            if r == 0:
                if c.basis == home[k]:
                    dets.append((bit(0, k),))
            else:
                dets.append((bit(r, k), bit(r - 1, k)))
        for j in range(nf):
            dets.append((fbit(r, j),))
    for k, c in enumerate(checks):
        if c.basis == home[k]:
            dets.append((bit(rounds - 1, k),) + tuple(D + d for d in sorted(c.support)))
    obs = tuple(tuple(D + d for d in sorted(lg)) for lg in logicals)
    return MemoryExperiment(name=name, code=code, distance=distance, rounds=rounds,
                            basis=label, n_data=n_data, n_ancilla=m + nf,
                            qasm="\n".join(out) + "\n", detectors=tuple(dets),
                            observables=obs, readout=readout,
                            readout_bits=(tuple((d, D + d) for d in range(n_data))
                                          if readout == "appended" else ()),
                            readout_basis=(tuple(dbasis) if mixed and readout == "appended"
                                           else ()))


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


def rotated_surface_xz(d: int, rounds: int | None = None, *,
                       readout: str = "appended") -> MemoryExperiment:
    """Two rotated surface patches in lockstep: a Z memory on data 0..n-1 and an X memory on
    n..2n-1, every round measuring both patches' checks layer by layer (see the module).
    Observable 0 is the Z patch's logical Z, observable 1 the X patch's logical X."""
    r = d if rounds is None else rounds
    p = patch(d, d, name=f"surface_d{d}")
    one = sorted(p.checks, key=lambda c: (c.basis != "X", int(c.name[1:])))
    n = p.n
    checks = [_Check(c.basis, tuple(q + off for q in c.support), tuple(c.layers))
              for off in (0, n) for c in one]
    logicals = [list(p.logical("Z")), [q + n for q in p.logical("X")]]
    return _memory(f"surface{d}xz_r{r}", f"rotated_surface_d{d}_x2", d, 2 * n, checks,
                   logicals, r, ["Z"] * n + ["X"] * n, layered=True, readout=readout)


@dataclass(frozen=True)
class _Phase:
    """One stretch of a surgery: the data it reads (measured in the basis named), then a
    transversal H on the data `hadamard` names, then the data states it moves (`move`: data q ->
    (the face whose measure ion carries it, the data q' it lands on), every face displaced by
    `shift`), then the data it prepares (reset into the basis named), then its checks --
    `(face, basis, support, layers)`, as `_placed` gives them -- measured `rounds` times."""
    checks: tuple
    rounds: int
    read: Mapping[int, str] = field(default_factory=dict)
    prep: Mapping[int, str] = field(default_factory=dict)
    hadamard: tuple = ()
    move: Mapping[int, tuple] = field(default_factory=dict)
    shift: tuple = (0, 0)


def _placed(rows: int, cols: int, at: tuple[int, int], index: Mapping) -> list:
    """`patch(rows, cols)`'s checks with its data qubit (0, 0) at lattice point `at`: each
    `(face, basis, support, layers)`, the face on the lattice and the support in data qubits
    (`index`: lattice point -> data qubit).  Patches at even offsets agree on every face's
    basis, so a seam one row or column wide between distance-d ones (odd d) joins them."""
    oi, oj = at
    return [((c.i + oi, c.j + oj), c.basis,
             tuple(index[(q // cols + oi, q % cols + oj)] for q in c.support), c.layers)
            for c in patch(rows, cols).checks]


#: how a check touches its corners: (corner order, the layer of the first).  Tomita–Svore is
#: `qccd.gadget.surface`'s.  `_DIAGONAL` measures every X check and then every Z check, each
#: touching one diagonal pair of corners and then the other: a fault on its measure ion then
#: spreads to a diagonal pair, two opposite edges of a face, which no shortest logical string
#: can use -- so a patch whose logicals have short representatives in both directions (a
#: corner moved into the middle of an edge) keeps its distance with no split schedule.  It
#: costs twice the layers.
_TOMITA = {"X": (("NW", "NE", "SW", "SE"), 0), "Z": (("NW", "SW", "NE", "SE"), 0)}
_DIAGONAL = {"X": (("NW", "SE", "NE", "SW"), 0), "Z": (("NW", "SE", "NE", "SW"), 4)}


def _region(at: tuple[int, int], rows: int, cols: int, edges: Mapping, index: Mapping,
            flip: int = 0, schedule: Mapping = _TOMITA) -> list:
    """The checks of a rows x cols patch whose top-left data point is `at`, as `_placed` gives
    them.  Face (i, j) is a Z face when i + j + flip is even (flip 1: the lattice under a patch
    after a transversal H).  Every weight-4 face is kept; on each side ("top", "right",
    "bottom", "left") the weight-2 faces of the basis `edges[side](k)` names, k counted along
    the side from its top or left end -- a side whose basis changes part-way has a corner in
    its middle."""
    oi, oj = at
    out = []
    for i in range(oi - 1, oi + rows):
        for j in range(oj - 1, oj + cols):
            basis = "Z" if (i + j + flip) % 2 == 0 else "X"
            corners = {k: (r, c) for k, (r, c) in (("NW", (i, j)), ("NE", (i, j + 1)),
                                                    ("SW", (i + 1, j)), ("SE", (i + 1, j + 1)))
                       if oi <= r < oi + rows and oj <= c < oj + cols}
            if len(corners) == 2:
                side, k = (("top", j - oj) if i < oi else ("bottom", j - oj) if i == oi + rows - 1
                           else ("left", i - oi) if j < oj else ("right", i - oi))
                if edges[side](k) != basis:
                    continue
            elif len(corners) != 4:
                continue
            order, first = schedule[basis]
            seq = [(index[corners[o]], first + t) for t, o in enumerate(order) if o in corners]
            out.append(((i, j), basis, tuple(q for q, _ in seq), tuple(t for _, t in seq)))
    return out


def _explained(qasm: str, logical: Mapping[int, str]) -> list[int]:
    """The classical bits whose parity, by stim's flow solver, is the eigenvalue of the Pauli
    `logical` (qubit -> "X" | "Z") on the state the circuit leaves: the corrections a readout of
    that logical at the end needs."""
    import stim
    c = ideal_stim(MemoryExperiment(name="flow", code="", distance=0, rounds=1, basis="Z",
                                    n_data=0, n_ancilla=0, qasm=qasm, detectors=(),
                                    observables=(), readout="appended"))
    ps = stim.PauliString(c.num_qubits)
    for q, p in logical.items():
        ps[q] = p
    found = c.solve_flow_measurements([stim.Flow(output=ps)])[0]
    if found is None:
        raise ValueError("the circuit does not leave an eigenstate of the logical")
    order = [op.bit for op in parse_qasm(qasm).ops if op.name == "M"]
    return sorted(order[m] for m in found)


def _phased(title: str, n: int, phases: Sequence[_Phase], final: Mapping[int, str]) -> tuple:
    """A surgery's QASM and detectors (see the module docstring), the face each ancilla
    measures (one per face, in qubit order), and where each classical bit went:
    `bits["check", phase, round, face]`, `bits["read", phase, q]` (data q read before that
    phase), `bits["final", q]` (q's appended readout, in the basis `final` names)."""
    anc: dict = {}
    for ph in phases:
        for f, *_ in sorted(ph.checks, key=lambda c: (c[1] != "X", c[0])):
            anc.setdefault(f, n + len(anc))
        for q in sorted(ph.move):
            anc.setdefault(ph.move[q][0], n + len(anc))
    bits: dict = {}

    def new(key) -> int:
        bits[key] = len(bits)
        return bits[key]

    out: list[str] = []
    dets: list[tuple[int, ...]] = []
    used: set = set()
    last: dict = {}                     # face -> (basis, support, its last bit) in the phase before
    for p, ph in enumerate(phases):
        for q, b in sorted(ph.read.items()):
            out += [f"h q[{q}];"] * (b == "X") + [f"measure q[{q}] -> c[{new(('read', p, q))}];"]
        if ph.hadamard:
            # every check on the H'd data changes type; the others are not compared again
            out += [f"h q[{q}];" for q in sorted(ph.hadamard)]
            last = {f: ({"X": "Z", "Z": "X"}[b], s, bit) for f, (b, s, bit) in last.items()
                    if s <= set(ph.hadamard)}
        if ph.move:
            # each state into its face's measure ion and out onto its new data ion, a reset
            # between: a fault touches one data state, never two
            mv = sorted(ph.move.items())
            out += [f"reset q[{anc[f]}];" for _, (f, _) in mv if f in used]
            out += [f"cx q[{q}],q[{anc[f]}];" for q, (f, _) in mv]
            out += [f"cx q[{anc[f]}],q[{q}];" for q, (f, _) in mv]
            out += [f"reset q[{q}];" for q in sorted({q for q, _ in mv} | {t for _, (_, t) in mv})]
            out += [f"cx q[{anc[f]}],q[{t}];" for _, (f, t) in mv]
            out += [f"cx q[{t}],q[{anc[f]}];" for _, (f, t) in mv]
            used.update(f for _, (f, _) in mv)
            to = {q: t for q, (_, t) in mv}
            di, dj = ph.shift
            last = {(f[0] + di, f[1] + dj): (b, frozenset(to[q] for q in s), bit)
                    for f, (b, s, bit) in last.items() if s <= to.keys()}
        out += [f"reset q[{q}];" for q in sorted(ph.prep) if p]
        out += [f"h q[{q}];" for q, b in sorted(ph.prep.items()) if b == "X"]
        checks = sorted(ph.checks, key=lambda c: (c[1] != "X", c[0]))
        for r in range(ph.rounds):
            out += [f"reset q[{anc[f]}];" for f, *_ in checks if f in used]
            out += [f"h q[{anc[f]}];" for f, b, *_ in checks if b == "X"]
            for t in sorted({t for c in checks for t in c[3]}):
                for f, b, sup, lay in checks:
                    out += [f"cx q[{anc[f]}],q[{q}];" if b == "X" else f"cx q[{q}],q[{anc[f]}];"
                            for q, lt in zip(sup, lay) if lt == t]
            out += [f"h q[{anc[f]}];" for f, b, *_ in checks if b == "X"]
            out += [f"measure q[{anc[f]}] -> c[{new(('check', p, r, f))}];" for f, *_ in checks]
            used.update(f for f, *_ in checks)
        touched = set(ph.read) | set(ph.prep)
        here = {f for f, *_ in checks}
        for f, (b0, s0, prev) in sorted(last.items()):
            if f not in here and all(ph.read.get(q) == b0 for q in s0):
                dets.append((prev,) + tuple(bits["read", p, q] for q in sorted(s0)))
        for f, b, sup, _ in checks:
            b0, s0, prev = last.get(f, (b, frozenset(), None))
            s = frozenset(sup)
            if (b0 == b and all(ph.prep.get(q) == b for q in s - s0)
                    and all(ph.read.get(q) == b for q in s0 - s) and not s & s0 & touched):
                dets.append((bits["check", p, 0, f],) + (() if prev is None else (prev,))
                            + tuple(bits["read", p, q] for q in sorted(s0 - s)))
        dets += [(bits["check", p, r, f], bits["check", p, r - 1, f])
                 for r in range(1, ph.rounds) for f, *_ in checks]
        last = {f: (b, frozenset(sup), bits["check", p, ph.rounds - 1, f]) for f, b, sup, _ in checks}
    D = len(bits)
    for q in sorted(final):
        new(("final", q))
    for f, b, sup, _ in sorted(phases[-1].checks, key=lambda c: (c[1] != "X", c[0])):
        if all(final.get(q) == b for q in sup):
            dets.append((last[f][2],) + tuple(bits["final", q] for q in sorted(sup)))
    head = ['OPENQASM 2.0;', 'include "qelib1.inc";',
            f"// {title}; data q[0..{n - 1}], ancillas q[{n}..{n + len(anc) - 1}]; "
            f"data readout c[{D}..{len(bits) - 1}] appended by the grader",
            f"qreg q[{n + len(anc)}];", f"creg c[{len(bits)}];"]
    faces = tuple(sorted(anc, key=anc.get))
    return "\n".join(head + out) + "\n", tuple(dets), bits, faces


def lattice_surgery(d: int, kind: str = "ZZ", rounds: int | None = None) -> MemoryExperiment:
    """A lattice surgery's measurement of Z̄_A Z̄_B ("ZZ") or X̄_A X̄_B ("XX") between two
    distance-d patches (`qccd.gadget.surface.merged_patch`): A and B start in |0̄> (|+̄>), the
    seam in |+> (|0>), the merged patch's checks run `rounds` (default d) times, and every data
    qubit is read at the end -- A and B in their basis, the seam in its own, which is the split.

    Detectors: in the first round, the checks of the patches' basis that do not touch the seam
    (deterministic); every later round, each check against the one before; at the end, those
    same checks against the data readout.  Observables: 0, the surgery's outcome -- the
    first-round product of the checks of its basis that touch the seam, which is Z̄_A Z̄_B
    (X̄_A X̄_B), +1 here; 1 and 2, Z̄_A and Z̄_B (X̄_A, X̄_B), which the measurement keeps."""
    from ..gadget.surface import merged_patch
    r = d if rounds is None else rounds
    if r < 1:
        raise ValueError("a lattice surgery needs at least one round")
    p, info = merged_patch(d, kind)
    mem = "Z" if kind == "ZZ" else "X"
    seam = set(info["seam"])
    coords = [p.coord(q) for q in range(p.n)]
    checks = _placed(p.rows, p.cols, (0, 0), {x: q for q, x in enumerate(coords)})
    dbasis = {q: info["seam_basis"] if q in seam else mem for q in range(p.n)}
    name = f"surgery{d}_{kind.lower()}_r{r}"
    qasm, dets, bits, faces = _phased(
        f"{name}: lattice surgery measuring {kind[0]}bar_A {kind[1]}bar_B between two d = {d} "
        f"patches, {r} round(s), {p.rows} x {p.cols} (A, the seam, B)",
        p.n, [_Phase(tuple(checks), r, prep=dbasis)], dbasis)
    measured = sorted(bits["check", 0, 0, f] for f, b, sup, _ in checks if b == mem and seam & set(sup))
    if kind == "ZZ":
        la, lb = [p.index(0, c) for c in range(d)], [p.index(2 * d, c) for c in range(d)]
    else:
        la, lb = [p.index(rr, 0) for rr in range(d)], [p.index(rr, 2 * d) for rr in range(d)]
    obs = (tuple(measured),) + tuple(tuple(bits["final", q] for q in sorted(lg)) for lg in (la, lb))
    return MemoryExperiment(name=name, code=f"rotated_surface_d{d}_{kind.lower()}",
                            distance=d, rounds=r, basis=mem, n_data=p.n, n_ancilla=len(faces),
                            qasm=qasm, detectors=dets, observables=obs, readout="appended",
                            readout_bits=tuple((q, bits["final", q]) for q in range(p.n)),
                            readout_basis=tuple(dbasis[q] for q in range(p.n)), coords=tuple(coords),
                            faces=faces)


def surgery_cnot(d: int, basis: str = "Z", rounds: int | None = None) -> MemoryExperiment:
    """A CNOT from control C to target T by lattice surgery through an ancilla patch A, as
    FormalRV writes it (Horsman et al. 2012): A starts in |+̄>; Z̄_C Z̄_A is measured (outcome
    m1), then X̄_A X̄_T (m2), then A is read in Z (m3); the frame takes Z̄_C when m2 and X̄_T when
    m1 + m3.

    Three distance-d patches on one lattice at pitch d + 1 -- C above A, T right of A -- the
    Z̄Z̄ seam the row between C and A, the X̄X̄ seam the column between A and T.  Phase 1:
    `rounds` (default d) rounds of the C + seam + A merged patch and of T's own checks.  Then
    the row seam is read in X and the column seam prepared in |0>.  Phase 2: `rounds` rounds
    of C's own checks and of the A + seam + T merged patch.  Then every data qubit left is
    read: A and the column seam in Z (m3, and the split), C and T in `basis`.

    `basis` "Z": C and T start in |0̄>; observable 0 is the flow the gate makes, Z̄_T ->
    Z̄_C Z̄_T with its correction m1 + m3 (A's top row, the seam qubit beside it and T's top row
    are the merged patch's Z̄, which phase 2 keeps), and observable 1 Z̄_C, which it keeps.
    "X": C and T start in |+̄>; observable 0 is X̄_C -> X̄_C X̄_T with m2 (and the row seam's
    readout in C's column, which the first split adds), observable 1 X̄_T.  Were the surgery
    not a CNOT, observable 0 would be random."""
    if basis not in ("X", "Z"):
        raise ValueError(f"basis must be 'X' or 'Z', not {basis!r}")
    r = d if rounds is None else rounds
    if r < 1:
        raise ValueError("a lattice surgery needs at least one round")
    C = [(i, j) for i in range(d) for j in range(d)]
    row = [(d, j) for j in range(d)]
    A = [(d + 1 + i, j) for i in range(d) for j in range(d)]
    col = [(d + 1 + i, d) for i in range(d)]
    T = [(d + 1 + i, d + 1 + j) for i in range(d) for j in range(d)]
    coords = sorted(C + row + A + col + T)
    index = {x: q for q, x in enumerate(coords)}

    def qs(points) -> list[int]:
        return [index[x] for x in points]

    zz = _placed(2 * d + 1, d, (0, 0), index) + _placed(d, d, (d + 1, d + 1), index)
    xx = _placed(d, d, (0, 0), index) + _placed(d, 2 * d + 1, (d + 1, 0), index)
    phases = [_Phase(tuple(zz), r, prep={**dict.fromkeys(qs(C + T), basis), **dict.fromkeys(qs(row + A), "X")}),
              _Phase(tuple(xx), r, read=dict.fromkeys(qs(row), "X"), prep=dict.fromkeys(qs(col), "Z"))]
    final = {**dict.fromkeys(qs(C + T), basis), **dict.fromkeys(qs(A + col), "Z")}
    name = f"cnot{d}_{basis.lower()}_r{r}"
    qasm, dets, bits, faces = _phased(
        f"{name}: a CNOT by lattice surgery, control C to target T through an ancilla patch A, "
        f"d = {d}, {r} round(s) a merge", len(coords), phases, final)

    def read(points) -> list[int]:
        return [bits["final", q] for q in qs(points)]

    if basis == "Z":
        seam = set(qs(row))
        m1 = [bits["check", 0, 0, f] for f, b, sup, _ in zz if b == "Z" and seam & set(sup)]
        flow = m1 + read([(d - 1, j) for j in range(d)]) + read([(d + 1, j) for j in range(2 * d + 1)])
        kept = read([(0, j) for j in range(d)])
    else:
        seam = set(qs(col))
        m2 = [bits["check", 1, 0, f] for f, b, sup, _ in xx if b == "X" and seam & set(sup)]
        flow = (m2 + [bits["read", 1, index[(d, d - 1)]]] + read([(i, d - 1) for i in range(d)])
                + read([(d + 1 + i, d + 1) for i in range(d)]))
        kept = read([(d + 1 + i, 2 * d) for i in range(d)])
    return MemoryExperiment(name=name, code=f"rotated_surface_d{d}_cnot", distance=d, rounds=r,
                            basis=basis, n_data=len(coords), n_ancilla=len(faces), qasm=qasm,
                            detectors=dets, observables=(tuple(sorted(flow)), tuple(sorted(kept))),
                            readout="appended",
                            readout_bits=tuple((q, bits["final", q]) for q in sorted(final)),
                            readout_basis=tuple(final[q] for q in sorted(final)),
                            coords=tuple(coords), faces=faces)


def hadamard_bar(d: int, basis: str = "Z", rounds: int | None = None) -> MemoryExperiment:
    """H̄ on a stationary distance-d patch, as Gehér, McLauchlan, Campbell, Moylett and Crawford
    build it (Quantum 8, 1394 (2024); arXiv:2312.11605), turned onto the machine's lattice: the
    patch's tile, its seam column and the neighbouring tile, d x (2d + 1) data points, the patch
    on the first d columns, Z̄ along row 0 and X̄ down column 0.

    0. the patch, prepared in `basis`, one round;
    1. a transversal H: every check changes type and the lattice under the patch is the other
       checkerboard (Z faces at odd i + j).  The patch grows over the seam and the neighbour
       (prepared |+>) to d x (2d + 1): Z along the top of the patch and the seam, X along the
       top of the neighbour, Z down the right and along the bottom, X up the left -- three
       corners on the top edge, one at the bottom-left; `rounds` (default d) rounds;
    2. the bottom becomes X: the corners are now at the top-left, top-middle, top-right and
       bottom-right, the patch's boundaries turned a quarter; `rounds` rounds;
    3. columns 0 .. d read in Z: the patch is the neighbour tile, X top and bottom; one round;
    4. it grows back over columns 1 .. d (prepared |0>); `rounds` rounds;
    5. columns d + 1 .. 2d read in Z: the patch on columns 1 .. d; one round;
    6. every data state moves one column left through the measure ion of the face between (two
       CNOTs in, a reset, two CNOTs out): the patch is home, on the machine's checkerboard,
       with X̄ and Z̄ exchanged; one round, and every data qubit is read in the other basis.

    Phases 1 and 2 use the `_DIAGONAL` schedule (eight layers): their logicals have short
    representatives in two directions at once (horizontal and vertical near the top corner);
    the four-layer schedules split by one diagonal line per phase, as the paper describes, that
    were tried here reach only d - 1 at d = 5 and 7.  Phase 6 is the paper's SWAP step without
    the round between its halves.

    Observable 0 is the gate's flow: Z̄ -> X̄ ("Z": prepared |0̄>, X̄ read at the end) or X̄ -> Z̄
    ("X"), the final readout of the output logical with the measurements that, by stim's flow
    solver, explain it."""
    if basis not in ("X", "Z"):
        raise ValueError(f"basis must be 'X' or 'Z', not {basis!r}")
    r = d if rounds is None else rounds
    if r < 1:
        raise ValueError("H-bar needs at least one round a phase")
    W = 2 * d + 1
    coords = [(i, j) for i in range(d) for j in range(W)]
    index = {x: q for q, x in enumerate(coords)}

    def cols(a: int, b: int) -> list[int]:
        return [index[(i, j)] for i in range(d) for j in range(a, b)]

    def sides(top, right: str, bottom: str, left: str) -> dict:
        return {"top": top, "right": lambda k: right, "bottom": lambda k: bottom,
                "left": lambda k: left}

    std = sides(lambda k: "X", "Z", "X", "Z")
    top = (lambda k: "Z" if k <= d else "X")          # Z over the patch and the seam
    home = _region((0, 0), d, d, std, index)
    grown = _region((0, 0), d, W, sides(top, "Z", "Z", "X"), index, 1, _DIAGONAL)
    turned = _region((0, 0), d, W, sides(top, "Z", "X", "X"), index, 1, _DIAGONAL)
    away = _region((0, d + 1), d, d, std, index, 1)
    back = _region((0, 1), d, 2 * d, std, index, 1)
    near = _region((0, 1), d, d, std, index, 1)
    other = "X" if basis == "Z" else "Z"
    phases = [
        _Phase(tuple(home), 1, prep=dict.fromkeys(cols(0, d), basis)),
        _Phase(tuple(grown), r, hadamard=tuple(cols(0, d)), prep=dict.fromkeys(cols(d, W), "X")),
        _Phase(tuple(turned), r),
        _Phase(tuple(away), 1, read=dict.fromkeys(cols(0, d + 1), "Z")),
        _Phase(tuple(back), r, prep=dict.fromkeys(cols(1, d + 1), "Z")),
        _Phase(tuple(near), 1, read=dict.fromkeys(cols(d + 1, W), "Z")),
        _Phase(tuple(home), 1, shift=(0, -1),
               move={index[(i, j)]: ((i, j - 1), index[(i, j - 1)])
                     for i in range(d) for j in range(1, d + 1)}),
    ]
    final = dict.fromkeys(cols(0, d), other)
    name = f"hbar{d}_{basis.lower()}_r{r}"
    qasm, dets, bits, faces = _phased(
        f"{name}: H-bar on a d = {d} patch, its seam and the neighbouring tile, {r} round(s) a "
        f"phase", len(coords), phases, final)
    out = ({index[(i, 0)]: "X" for i in range(d)} if other == "X"
           else {index[(0, j)]: "Z" for j in range(d)})
    flow = _explained(qasm, out) + [bits["final", q] for q in out]
    return MemoryExperiment(name=name, code=f"rotated_surface_d{d}_hbar", distance=d, rounds=r,
                            basis=basis, n_data=len(coords), n_ancilla=len(faces), qasm=qasm,
                            detectors=dets, observables=(tuple(sorted(flow)),), readout="appended",
                            readout_bits=tuple((q, bits["final", q]) for q in sorted(final)),
                            readout_basis=tuple(final[q] for q in sorted(final)),
                            coords=tuple(coords), faces=faces)


_SIDE = {"top": (-1, 0), "bottom": (1, 0), "left": (0, -1), "right": (0, 1)}
_FAR = {"top": "bottom", "bottom": "top", "left": "right", "right": "left"}


def _tile(d: int, t: tuple[int, int]) -> set:
    """The data points of tile (x, y) on the machine's lattice (pitch d + 1)."""
    x, y = t
    return {(y * (d + 1) + i, x * (d + 1) + j) for i in range(d) for j in range(d)}


def _seam(d: int, a: tuple[int, int], b: tuple[int, int]) -> set:
    """The seam between two neighbouring tiles."""
    (x1, y1), (x2, y2) = sorted([a, b])
    p = d + 1
    if y1 == y2 and x2 == x1 + 1:
        return {(y1 * p + i, x1 * p + d) for i in range(d)}
    if x1 == x2 and y2 == y1 + 1:
        return {(y1 * p + d, x1 * p + j) for j in range(d)}
    raise ValueError(f"tiles {a} and {b} are not neighbours")


def _cut_corners(pts: set, keep: set, letter: str) -> set:
    """`pts` with every convex corner point outside `keep` removed whose two corner faces are not
    of the type `letter` -- on a boundary all of that type such a corner would be a point of the
    other type, a logical qubit too many; cut, its face becomes a weight-3 check of the right one."""
    want = 0 if letter == "Z" else 1
    pts = set(pts)
    while True:
        cut = set()
        for r, c in pts - keep:
            up, down, left, right = ((r - 1, c) in pts, (r + 1, c) in pts, (r, c - 1) in pts,
                                     (r, c + 1) in pts)
            par = (r + c - 1 if (not up and not left) or (not down and not right)
                   else r + c if (not up and not right) or (not down and not left) else None)
            if par is not None and par % 2 != want:
                cut.add((r, c))
        if not cut:
            return pts
        pts -= cut


def corridor_merge(d: int, letter: str, patches: Sequence[tuple], corridor,
                   rounds: int | None = None) -> MemoryExperiment:
    """A lattice surgery measuring the product of Z̄ ("Z") or X̄ ("X") of any number of distance-d
    patches through a corridor of free tiles, on the machine's lattice: tile (x, y) holds data rows
    y(d + 1) .. y(d + 1) + d - 1 and columns x(d + 1) .. x(d + 1) + d - 1, a seam row and column
    between tiles; a patch has Z̄ along a row and X̄ down a column.

    `patches`: (tile, side) each -- "top" or "bottom" for a Z̄ product, "left" or "right" for X̄:
    the patch joins, through the seam on that side, the corridor tile there.  `corridor`: a
    connected set of tiles.  The merged region is the patches, those seams, the corridor and the
    seams (and seam crossings with three corridor tiles around) inside it; every point outside a
    patch starts in |+> ("Z") or |0> ("X").  Each patch's far side keeps its own boundary; the
    rest of the region's boundary is of the merge's type, with its corners of the wrong
    checkerboard colour cut (`_cut_corners`).  The checks run `rounds` (default d) times, in the
    Tomita–Svore schedule; then every data point is read, the patches in the merge's basis.

    Observables: 0, the product -- the first-round product of the merge-type checks that touch
    the fresh points, +1 here (the patches start in |0̄> or |+̄>); then each patch's own Z̄ (X̄),
    on its far side, which the merge keeps."""
    if letter not in ("X", "Z"):
        raise ValueError(f"letter must be 'X' or 'Z', not {letter!r}")
    r = d if rounds is None else rounds
    corridor = set(corridor)
    ok = ("top", "bottom") if letter == "Z" else ("left", "right")
    pts: set = set()
    held: list = []
    for t, side in patches:
        if side not in ok:
            raise ValueError(f"a {letter}-bar product joins a patch on its {' or '.join(ok)}, not {side}")
        nb = (t[0] + _SIDE[side][1], t[1] + _SIDE[side][0])
        if nb not in corridor:
            raise ValueError(f"patch {t} joins the corridor on its {side}, at {nb}: not a corridor tile")
        held.append(_tile(d, t))
        pts |= held[-1] | _seam(d, t, nb)
    p = d + 1
    for x, y in corridor:
        pts |= _tile(d, (x, y))
        pts |= set().union(*(_seam(d, (x, y), n) for n in ((x + 1, y), (x, y + 1)) if n in corridor))
    for cx, cy in {(x - i, y - j) for x, y in corridor for i in (0, 1) for j in (0, 1)}:
        if sum(t in corridor for t in ((cx, cy), (cx + 1, cy), (cx, cy + 1), (cx + 1, cy + 1))) >= 3:
            pts.add((cy * p + d, cx * p + d))
    inside = set().union(*held)
    pts = _cut_corners(pts, inside, letter)
    other = "X" if letter == "Z" else "Z"
    far: set = set()                    # the faces on each patch's far side
    for (x, y), side in patches:
        f = _FAR[side]
        far |= ({(y * p - 1 if f == "top" else y * p + d - 1, x * p + j) for j in range(-1, d)}
                if f in ("top", "bottom") else
                {(y * p + i, x * p - 1 if f == "left" else x * p + d - 1) for i in range(-1, d)})
    coords = sorted(pts)
    index = {q: k for k, q in enumerate(coords)}
    checks = []
    for i in range(min(a for a, _ in pts) - 1, max(a for a, _ in pts) + 1):
        for j in range(min(b for _, b in pts) - 1, max(b for _, b in pts) + 1):
            basis = "Z" if (i + j) % 2 == 0 else "X"
            corners = {k: q for k, q in (("NW", (i, j)), ("NE", (i, j + 1)), ("SW", (i + 1, j)),
                                         ("SE", (i + 1, j + 1))) if q in pts}
            if (len(corners) < 2 or (len(corners) == 2 and basis != (other if (i, j) in far else letter))
                    or (len(corners) == 3 and basis != letter)):
                continue
            order, first = _TOMITA[basis]
            seq = [(index[corners[o]], first + t) for t, o in enumerate(order) if o in corners]
            checks.append(((i, j), basis, tuple(q for q, _ in seq), tuple(t for _, t in seq)))
    prep = {index[q]: letter if q in inside else other for q in coords}
    name = f"corridor{d}_{letter.lower()}{len(patches)}_r{r}"
    qasm, dets, bits, faces = _phased(
        f"{name}: lattice surgery measuring the product of {letter}bar on {len(patches)} d = {d} "
        f"patches through {len(corridor)} corridor tile(s), {r} round(s)",
        len(coords), [_Phase(tuple(checks), r, prep=prep)], prep)
    fresh = {index[q] for q in coords if q not in inside}
    obs = [tuple(sorted(bits["check", 0, 0, f] for f, b, sup, _ in checks if b == letter and fresh & set(sup)))]
    for (x, y), side in patches:
        f = _FAR[side]
        lg = ([(y * p if f == "top" else y * p + d - 1, x * p + j) for j in range(d)] if letter == "Z"
              else [(y * p + i, x * p if f == "left" else x * p + d - 1) for i in range(d)])
        obs.append(tuple(sorted(bits["final", index[q]] for q in lg)))
    return MemoryExperiment(name=name, code=f"rotated_surface_d{d}_corridor", distance=d, rounds=r,
                            basis=letter, n_data=len(coords), n_ancilla=len(faces), qasm=qasm,
                            detectors=dets, observables=tuple(obs), readout="appended",
                            readout_bits=tuple((q, bits["final", q]) for q in range(len(coords))),
                            readout_basis=tuple(prep[q] for q in range(len(coords))),
                            coords=tuple(coords), faces=faces)


def css_memory_xz(code: CSSCode, rounds: int, name: str | None = None, *,
                  distance: int | None = None, flags: bool = False,
                  readout: str = "appended") -> MemoryExperiment:
    """Two copies of a CSS code side by side, a Z memory on data 0..n-1 and an X memory on
    n..2n-1 (the construction of `rotated_surface_xz`, checks one after another as
    `css_memory` measures them).  Observables: the code's k Z logicals, then its k X logicals."""
    if distance is None:
        declared = code.params.get("declared")
        if not declared:
            raise ValueError(f"{code.name}: pass `distance`; the code declares none")
        distance = int(declared[2])
    lx, lz = code.logical_basis
    n = code.n
    one = ([_Check("X", tuple(gf2.support(h))) for h in code.hx]
           + [_Check("Z", tuple(gf2.support(h))) for h in code.hz])
    checks = [_Check(c.basis, tuple(q + off for q in c.support)) for off in (0, n) for c in one]
    logicals = ([list(gf2.support(row)) for row in lz]
                + [[q + n for q in gf2.support(row)] for row in lx])
    return _memory(name or f"{code.name}xz_r{rounds}", f"{code.name}_x2", distance, 2 * n, checks,
                   logicals, rounds, ["Z"] * n + ["X"] * n, layered=False, flags=flags,
                   note="flagged" if flags else "", readout=readout)


def css_memory_seq(code: CSSCode, rounds: int, name: str | None = None, *,
                   distance: int | None = None, flags: bool = False) -> MemoryExperiment:
    """ONE block of a CSS code, a Z memory and then an X memory on the same ions: for a code whose
    two copies would not fit the ions it is measured on (bb72's 144, bb90's 180, under Goal 1's
    200).  Phase 1 reads its data out IN the program (Z); every ion is then reset and phase 2
    prepares |+> and is read out by the grader (X).  The 2k observables are phase 1's Z logicals
    and phase 2's X logicals, so `basis` is "XZ" and `logical_qubits` k, as for two copies; a
    round is one phase's, so the rate per round compares with a two-copy experiment's."""
    zm = css_memory(code, rounds, basis="Z", distance=distance, flags=flags, readout="program")
    xm = css_memory(code, rounds, basis="X", distance=distance, flags=flags, readout="appended")
    zl, xl = zm.qasm.splitlines(), xm.qasm.splitlines()
    nq = zm.n_data + zm.n_ancilla
    bz = parse_qasm(zm.qasm).n_bits
    bx = parse_qasm(xm.qasm).n_bits

    def body(lines: list) -> list:
        return [ln for ln in lines if ln and not ln.startswith(("OPENQASM", "include", "//", "qreg", "creg"))]

    import re
    shift = [re.sub(r"c\[(\d+)\]", lambda mm: f"c[{int(mm.group(1)) + bz}]", ln) for ln in body(xl)]
    nm = name or f"{code.name}seq_r{rounds}"
    out = (['OPENQASM 2.0;', 'include "qelib1.inc";',
            f"// {nm}: {code.name} memory, one block: a Z memory of {rounds} round(s) read out in the "
            f"program, every ion reset, then an X memory of {rounds} round(s); data q[0..{zm.n_data - 1}]; "
            f"X readout c[{bz + bx - zm.n_data}..{bz + bx - 1}] appended by the grader",
            f"qreg q[{nq}];", f"creg c[{bz + bx}];"]
           + body(zl) + [f"reset q[{j}];" for j in range(nq)] + shift)
    off = lambda bits: tuple(b + bz for b in bits)  # noqa: E731
    return MemoryExperiment(
        name=nm, code=f"{code.name}_seq", distance=zm.distance, rounds=rounds, basis="XZ",
        n_data=zm.n_data, n_ancilla=zm.n_ancilla, qasm="\n".join(out) + "\n",
        detectors=zm.detectors + tuple(off(d) for d in xm.detectors),
        observables=zm.observables + tuple(off(o) for o in xm.observables),
        readout="appended", readout_bits=tuple((q, b + bz) for q, b in xm.readout_bits),
        readout_basis=("X",) * len(xm.readout_bits))


def shor_code() -> CSSCode:
    """Shor's [[9,1,3]]: Z checks on neighbours within each block of three, X checks across blocks."""
    rows = lambda sets: tuple(sum(1 << q for q in s) for s in sets)  # noqa: E731
    return CSSCode(name="shor", n=9, hx=rows(((0, 1, 2, 3, 4, 5), (3, 4, 5, 6, 7, 8))),
                   hz=rows(((0, 1), (1, 2), (3, 4), (4, 5), (6, 7), (7, 8))), family="shor",
                   params={"declared": [9, 1, 3]})


def repetition_code(d: int) -> CSSCode:
    """The bit-flip repetition code as a CSS code: Z checks only.  In both bases it protects bit
    flips to distance d and phase flips not at all (distance 1)."""
    return CSSCode(name=f"rep{d}", n=d, hx=(), hz=tuple((1 << i) | (1 << (i + 1)) for i in range(d - 1)),
                   family="repetition", params={"declared": [d, 1, 1]})


def bb_code(name: str) -> CSSCode:
    l, m, a, b, (n, k, d) = TUTORIAL_BB[name]
    return bivariate_bicycle(name, l, m, parse_poly(a, l, m), parse_poly(b, l, m), declared=(n, k, d))


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
    "surface3xz": lambda: rotated_surface_xz(3),
    "surface5xz": lambda: rotated_surface_xz(5),
    "surface7xz": lambda: rotated_surface_xz(7),
    # Goal 1's other codes, both bases (two copies, or one block twice in a row)
    "steanexz": lambda: css_memory_xz(steane_code(), 3, "steanexz_r3", distance=3, flags=True),
    "shorxz": lambda: css_memory_xz(shor_code(), 3, "shorxz_r3", distance=2, flags=True),  # circuit distance 2
    "rep3xz": lambda: css_memory_xz(repetition_code(3), 3, "rep3xz_r3", distance=1),
    "rep5xz": lambda: css_memory_xz(repetition_code(5), 5, "rep5xz_r5", distance=1),
    "bb18xz": lambda: css_memory_xz(bb_code("bb18"), 4, "bb18xz_r4", distance=4, flags=True),
    "bb72seq": lambda: css_memory_seq(bb_code("bb72"), 3, "bb72seq_r3", distance=6),
    "bb90seq": lambda: css_memory_seq(bb_code("bb90"), 3, "bb90seq_r3", distance=10),
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
    probability p, then a measurement in the data qubit's basis.
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
    for q, b, basis in exp.readout_plan():
        append_readout(c, basis, [q], p)
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
