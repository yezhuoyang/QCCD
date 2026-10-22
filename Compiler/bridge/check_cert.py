"""R10: does the compiled program implement the input circuit?

`qccd/verify/__init__.py` lists R10 as UNCHECKABLE with the reason *"needs symbolic
permutation + Pauli-frame tracking against a QASM DAG"*.  This is the check that reason
describes.  It has two halves, and neither of them asks the compiler what it intended.

**O1 -- the certificate's witnesses: transport and co-location.**  Ion positions are
recomputed by replaying the certificate's move and rotation witnesses from its initial
placement.  Every GATE WITNESS -- the certificate's claim that circuit op `dag` ran at
cycle `t` in trap `site` -- must find its ions already together in that trap, and the trap
must be gate-capable; the proved Lean checker (`--qcheck`, `QCCDC.Cert.check_sound`)
decides the same question of the same witnesses -- on an input that must equal the one
rebuilt here from this certificate and this device (`qcheck_binding`), so its verdict is
this certificate's and not some other's.  This half is about the witnesses, each of which
the Lean checker also holds to its op's own ions (`OwnIons`); what ties them to the emitted
program is `check_witnesses`: an instruction of its op's kind on those ions.  Positions the
certificate also claims are cross-checked, never believed -- the discipline
`qccd/ir/import_deck.py` applies to the shipped artifact, for the same reason.

The witnesses index `circuit_ops`, the certificate's own copy of the (lowered) circuit, so
O1 -- and the Lean verdict -- are only about the source circuit if that list IS the source
circuit.  `check_binding` checks it against the QASM op by op, together with the qubit
count and the qubit->ion map, and a disagreement fails R10.

**O2/O3 -- semantics.**  The compiled program's meaning is recovered *from the emitted
hardware program itself*: the native pulses (`R`, `VZ`, `MS`) are read out of the TSIR,
composed over the logical qubits through the certificate's qubit->ion map, and compared
with the SOURCE circuit as parsed from the QASM (`load_source`) -- never with
`circuit_ops`, which is the compiler's account of what it compiled.  For a Clifford
circuit (decision D1) both sides are stabilizer tableaux.  Outside the Clifford fragment,
for a circuit of at most `--max-unitary-qubits` qubits, both are exact unitaries compared
up to one global phase, and a composite such as `ccx` enters at its defining matrix, so
the compiler's lowering table is checked rather than trusted.  Nothing about the
compiler's reasoning enters.  A swapped operand, a dropped gate, a wrong rotation angle or
a mis-tracked frame all move the tableau, or the unitary.

This subsumes Pauli-frame tracking rather than implementing it separately, which is the
whole argument for D1: a frame error IS a tableau difference.  Measurements and resets are
compared too, in their deferred form on both sides (`readout_plan`): the `j`-th measurement
of a qubit is a CNOT onto a fresh qubit, its `j`-th reset a SWAP with one, so a readout on
the wrong ion, at an inequivalent point, or one the circuit does not have moves the tableau
or the unitary like any gate.  `barrier` is skipped on both sides.

    python Compiler/bridge/check_cert.py build/out/ghz8_grid9x9 \
        --qasm bench/ghz8.qasm --arch arch/grid9x9.arch.json
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qccd.arch import Architecture  # noqa: E402
from qccd.ir.tsir import TSIR  # noqa: E402

PI = math.pi
#: the pulses the hardware executes, and so the only gates the semantic half can compose
NATIVE = ("R", "VZ", "MS")
#: what both semantic routes skip: readout, re-preparation and fences are not unitaries
NON_UNITARY = ("measure", "reset", "barrier")

_BRIDGE_MODULES: dict = {}


def _bridge(name: str):
    """A script beside this file, loaded by path: this module is imported by path too
    (`qccd/bench/check.py`, `qccd/workspace/evaluator.py`), when its directory need not be
    on `sys.path`."""
    if name not in _BRIDGE_MODULES:
        spec = importlib.util.spec_from_file_location(f"qccd_check_cert_{name}", HERE / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _BRIDGE_MODULES[name] = mod
    return _BRIDGE_MODULES[name]


def _unitary():
    return _bridge("unitary")


def find_qcheck(lean: Path) -> Path | None:
    """The built Lean checker: `QCCD_QCHECK`, else the standalone `lean/checker` package's
    build, else the full package's; `qcheck.exe` on Windows, `qcheck` elsewhere."""
    if os.environ.get("QCCD_QCHECK"):
        return Path(os.environ["QCCD_QCHECK"])
    for d in (lean / "checker" / ".lake" / "build" / "bin", lean / ".lake" / "build" / "bin"):
        for name in ("qcheck.exe", "qcheck"):
            if (d / name).is_file():
                return d / name
    return None


def qcheck_binding(qcheck_input: Path, cert: dict, arch: Architecture) -> list[str]:
    """Was `qcheck_input` made from THIS certificate on THIS device?  The problems, empty
    if so.

    The Lean checker decides whatever input it is given, and its verdict is reported as
    this certificate's.  So the input is rebuilt here -- `mk_qcheck_input.build` over the
    certificate and `export_arch.expand` of the device this check was given -- and the file
    must equal it.  An input made from another program, or from another device, is refused
    rather than believed.
    """
    want = json.loads(json.dumps(_bridge("mk_qcheck_input").build(cert, _bridge("export_arch").expand(arch))))
    try:
        got = json.loads(Path(qcheck_input).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"the qcheck input does not load: {exc}"]
    if got == want:
        return []
    if not isinstance(got, dict):
        return ["the qcheck input is not a qcheck input"]
    fields = sorted(k for k in set(want) | set(got) if want.get(k) != got.get(k))
    return [f"the qcheck input was not made from this certificate on this device: it differs in "
            f"{', '.join(fields)}"]


# ------------------------------------------------------------------ O1


def replay_positions(cert: dict, arch: Architecture) -> tuple[dict, list[str]]:
    """Recompute where every ion is at every cycle, from init + the movement witnesses.

    Movement is of two kinds. A `move` carries one ion across one segment. A `rotation`
    turns a whole loop: every ion standing ON it advances by `delta` slots, and every ion
    that is not -- a docked one -- stays where it is. That asymmetry is why the rotation
    compiler must never turn the loop while a dock is occupied, and modelling it here is
    what makes the replay able to catch it if it ever did.

    The loop's node order is read from `arch`, never from the certificate: a compiler that
    supplied its own could rotate ions to wherever its gates needed them.
    """
    problems: list[str] = []
    pos = dict(cert["init"])
    loops = {
        lid: list(l.nodes)
        for lid, l in getattr(arch.device, "loops", {}).items()
        if l.closed
    }

    by_cycle: dict[int, list] = {}
    for m in cert["moves"]:
        by_cycle.setdefault(m["t"], []).append(m)
    rots_at: dict[int, list] = {}
    for r in cert.get("rotations", []):
        rots_at.setdefault(r["t"], []).append(r)

    last = max([0] + [m["t"] for m in cert["moves"]] + [g["t"] for g in cert["gates"]]
               + [r["t"] + abs(r["delta"]) for r in cert.get("rotations", [])])
    snapshots: dict[int, dict] = {}
    for t in range(last + 1):
        snapshots[t] = dict(pos)
        for m in by_cycle.get(t, []):
            if pos.get(m["ion"]) != m["from"]:
                problems.append(
                    f"t={t}: {m['ion']} departs {m['from']} but is at {pos.get(m['ion'])}")
            pos[m["ion"]] = m["to"]
        for r in rots_at.get(t, []):
            nodes = loops.get(r["loop"])
            if nodes is None:
                problems.append(
                    f"t={t}: rotation of {r['loop']}, which the architecture does not have")
                continue
            slot = {s: i for i, s in enumerate(nodes)}
            n = len(nodes)
            for ion, site in list(pos.items()):
                if site in slot:
                    pos[ion] = nodes[(slot[site] + r["delta"]) % n]
    return snapshots, problems


def check_o1(cert: dict, arch: Architecture) -> list[str]:
    snapshots, problems = replay_positions(cert, arch)
    for g in cert["gates"]:
        at = snapshots.get(g["t"], {})
        sites = {ion: at.get(ion) for ion in g["ions"]}
        if len(set(sites.values())) != 1 or None in sites.values():
            problems.append(
                f"op {g['dag']}: operands not co-located at t={g['t']}: {sites}")
            continue
        site = next(iter(sites.values()))
        if site != g["site"]:
            problems.append(
                f"op {g['dag']}: witness says {g['site']}, replay says {site}")
        if not arch.can(site, "gate"):
            zt = arch.device.nodes[site].zone_type
            problems.append(
                f"op {g['dag']}: gate at {site}, whose zone {zt!r} cannot gate")
    return problems


def check_witnesses(cert: dict, prog: TSIR) -> list[str]:
    """Does every witness describe the program it certifies?  The problems, empty if so.

    The Lean checker relates a witness to the circuit op it names and to that op's own ions
    (`OwnIons`), never to the program: a witness for a measurement naming a gate pulse is
    accepted there.  So, against the program and the qubit->ion map:

    * a witness names the ions its op's qubits are mapped to, no more and no fewer -- which
      the Lean checker proves too, and which this check holds without `--qcheck` as well;
    * the instruction it names exists, and is of its op's kind -- a gate pulse on at least
      one of those ions for a gate, a measurement or reset of the ion for a measurement or
      reset;
    * no witness names a barrier, which is a fence no instruction performs.
    """
    by_id = {i.id: i for i in prog.instructions}
    ops = {o.get("i"): o for o in cert.get("circuit_ops", [])}
    qmap = cert.get("map", {})
    problems: list[str] = []
    for g in cert.get("gates", []):
        op = ops.get(g.get("dag"))
        if op is None:
            problems.append(f"witness for op {g.get('dag')}: the certificate has no such op")
            continue
        what = f"witness for op {op['i']} ({op['name']} {op['qubits']})"
        want = sorted(str(qmap.get(str(q), f"<qubit {q}>")) for q in op["qubits"])
        if sorted(map(str, g.get("ions", []))) != want:
            problems.append(f"{what}: names ions {g.get('ions')}, its qubits are on {want}")
        ins = by_id.get(g.get("instr"))
        if op["name"] == "barrier":
            problems.append(f"{what}: a barrier is a fence, which no instruction performs")
        elif ins is None:
            problems.append(f"{what}: names instruction {g.get('instr')}, which the program does not have")
        else:
            on = set(ins.ions) | {x for p in ins.pairs for x in p}
            if op["name"] in ("measure", "reset"):
                if ins.type != op["name"] or not set(want) <= set(ins.ions):
                    problems.append(f"{what}: names instruction {ins.id}, a {ins.type} on "
                                    f"{sorted(on)}, not a {op['name']} of {want}")
            elif ins.type != "gate" or not (on & set(want)):
                problems.append(f"{what}: names instruction {ins.id}, a {ins.type} on "
                                f"{sorted(on)}, not a gate pulse on {want}")
        if len(problems) >= 6:
            break
    return problems


def check_program_transport(cert: dict, prog: TSIR) -> list[str]:
    """Does the programme move ions exactly as its certificate says?  The problems, empty if so.

    The proved checker replays the CERTIFICATE's moves, and the witnesses are checked against
    that replay; nothing else tied those moves to the programme's own transport, so a
    programme could move an ion the certificate keeps still (or the other way round) and O1
    would still hold of the certificate.  Here the two are set side by side: every ion makes
    the same moves in the same order -- from the same site, to the same site, along the same
    rails when both name them -- and the same loops are turned by the same amounts in the
    same order.

    Order, not time: a certificate's `t` is the compiler's own clock, on which a rotation by
    k slots takes k cycles and one cycle may hold several instructions, so it is not an
    instruction number.  Where an ion is when it gates is held to the programme by the rules
    (R6b, at the replayed position) and to the certificate by O1.
    """
    got: dict[str, list] = {}
    got_rot: list = []
    for ins in prog.instructions:
        if ins.type != "simd":
            continue
        tpl = ins.template or {}
        if tpl.get("kind") == "loop_shift":
            got_rot.append((str(tpl.get("loop")), int(tpl.get("delta", 0))))
        for q in ins.participants:
            got.setdefault(str(q.ion), []).append((str(q.src), str(q.dst), tuple(q.via or ())))
    want: dict[str, list] = {}
    for m in sorted(cert.get("moves", []), key=lambda m: int(m["t"])):
        want.setdefault(str(m["ion"]), []).append((str(m["from"]), str(m["to"]), tuple(m.get("via") or ())))
    want_rot = [(str(r["loop"]), int(r["delta"]))
                for r in sorted(cert.get("rotations", []), key=lambda r: int(r["t"]))]
    problems: list[str] = []
    for ion in sorted(set(got) | set(want)):
        g, w = got.get(ion, []), want.get(ion, [])
        if len(g) != len(w):
            problems.append(f"{ion} makes {len(g)} moves in the programme and {len(w)} in the certificate")
            continue
        for k, (a, b) in enumerate(zip(g, w)):
            if a[:2] != b[:2] or (a[2] and b[2] and a[2] != b[2]):
                problems.append(f"{ion}'s move {k + 1}: the programme {a[0]} -> {a[1]} via {list(a[2])}, "
                                f"the certificate {b[0]} -> {b[1]} via {list(b[2])}")
                break
        if len(problems) >= 6:
            return problems
    if got_rot != want_rot:
        problems.append(f"the programme turns the loops {got_rot[:4]}{'...' if len(got_rot) > 4 else ''}, "
                        f"the certificate {want_rot[:4]}{'...' if len(want_rot) > 4 else ''}")
    return problems


def check_program_order(cert: dict, prog: TSIR) -> list[str]:
    """Does every qubit run its own ops in the circuit's order?  The problems, empty if so.

    STRICT: two ops on one qubit must run in their source order even when they commute.
    The order of a qubit's gates is the circuit author's to choose -- in a syndrome round
    it is the hook structure -- and the semantic checks cannot see a reorder, since a
    tableau or a unitary is the same either way.  A compiler that batched a check's CNOTs
    by rotation offset passed every one of them with 726 adjacent pairs out of order on a
    BB144 round.

    Read from the program: every gate pulse, measurement and reset instruction lists the
    ops it performs (`meta.op`), and a gate's witness names its last pulse.  For each
    qubit, every instruction of an op that touches that qubit must come after every
    instruction of the qubit's previous op that touches it; pulses on OTHER qubits may
    overlap, which is parallelism, not reordering.  A barrier is a fence across its wires:
    nothing after it on any of them starts before everything before it on all of them has
    ended.  A label that names an op whose ions the instruction does not touch is itself
    a problem, so the positions cannot be borrowed from another op.
    """
    qmap = {int(q): str(ion) for q, ion in cert.get("map", {}).items()}
    ops = cert.get("circuit_ops", [])
    if not ops:
        return ["the certificate carries no copy of the circuit (circuit_ops), so no "
                "qubit's order can be read"]
    by_op = {o.get("i"): o for o in ops}
    pos_of_id = {ins.id: k for k, ins in enumerate(prog.instructions)}
    problems: list[str] = []
    # (op, ion) -> positions of the instructions that perform the op and touch the ion
    where: dict[tuple[int, str], list[int]] = {}

    def note(i: int, k: int, on: set[str], why: str) -> None:
        op = by_op.get(i)
        if op is None:
            problems.append(f"{why} names op {i}, which the certificate does not have")
            return
        mine = {qmap.get(q) for q in op["qubits"]} & on
        if not mine:
            problems.append(f"{why} names op {i} ({op['name']} {op['qubits']}) but touches "
                            f"none of its ions")
        for ion in mine:
            where.setdefault((i, ion), []).append(k)

    # Programs from before 2026-09 label no measurement or reset with its op.  Such an
    # instruction stands for its ion's next unlabelled readout of that kind in the
    # circuit's order -- the pairing `readout_plan` makes -- so the readouts keep their
    # order among themselves and are still checked against the gates around them.
    ion_qubit = {ion: q for q, ion in qmap.items()}
    labelled = {int(i) for ins in prog.instructions if ins.type in ("measure", "reset")
                for i in (ins.meta or {}).get("op", []) or []}
    pending: dict[tuple[int, str], list[int]] = {}
    for o in ops:
        if o["name"] in ("measure", "reset") and o["i"] not in labelled:
            pending.setdefault((o["qubits"][0], o["name"]), []).append(o["i"])
    for k, ins in enumerate(prog.instructions):
        if ins.type not in ("gate", "measure", "reset"):
            continue
        on = set(ins.ions) | {x for p in ins.pairs for x in p}
        label = (ins.meta or {}).get("op")
        if label is None and ins.type in ("measure", "reset"):
            label = [pending[(ion_qubit[ion], ins.type)].pop(0) for ion in ins.ions
                     if pending.get((ion_qubit.get(ion), ins.type))]
        for i in label or []:
            note(int(i), k, on, f"instruction {ins.id}")
    for g in cert.get("gates", []):
        if g.get("instr") in pos_of_id:
            note(int(g["dag"]), pos_of_id[g["instr"]], set(map(str, g.get("ions", []))),
                 f"the witness for op {g['dag']}")
    if problems:
        return problems[:6]

    unrealised = set(cert.get("unrealised", []))
    free_after: dict[str, int] = {}      # ion -> the position its next op must come after
    last: dict[str, dict] = {}           # ion -> its previous op, to say what was overtaken
    swaps = 0
    for o in ops:
        ions = [qmap.get(q) for q in o["qubits"]]
        if o["name"] == "barrier":
            fence = max((free_after.get(ion, -1) for ion in ions), default=-1)
            for ion in ions:
                free_after[ion] = fence
            continue
        if o["i"] in unrealised:
            continue
        for ion in ions:
            ks = where.get((o["i"], ion))
            if not ks:
                problems.append(f"op {o['i']} ({o['name']} {o['qubits']}): no instruction "
                                f"performs it on {ion}")
                continue
            if min(ks) <= free_after.get(ion, -1):
                swaps += 1
                if len(problems) < 6:
                    prev = last.get(ion)
                    after = (f"op {prev['i']} ({prev['name']} {prev['qubits']})" if prev
                             else "a barrier before it")
                    problems.append(f"{ion}: op {o['i']} ({o['name']} {o['qubits']}) starts "
                                    f"before {after} has finished")
            free_after[ion] = max(free_after.get(ion, -1), max(ks))
            last[ion] = o
    if swaps > 6:
        problems.append(f"... {swaps} ops in all start before an earlier op on one of their qubits")
    return problems


# ------------------------------------------------------------------ O2 / O3


def is_right_angle(x: float, eps: float = 1e-9) -> bool:
    return abs(x / (PI / 2) - round(x / (PI / 2))) < eps


#: the parameters a pulse carries when the program writes none
_NO_PARAMS = {"R": (0.0, 0.0), "VZ": (0.0,), "MS": (0.0,)}


def program_steps(prog: TSIR, qubit_of: dict[str, int]):
    """The EMITTED program, read over the logical qubits: `(gate, qubits, params)` for each
    native pulse, `("measure" | "reset", (qubit,), ())` for each ion a readout or reset
    instruction names, in program order.  The qubit is None for an ion that carries none.

    The operands are the IR's own (`qccd.ir.tsir.iter_operands`), so every spelling the
    program format allows is read: an `MS` written as two `ions` rather than `pairs` is one
    pair, not nothing.  Anything that cannot be read exactly raises NotImplementedError --
    a pulse skipped here would be a program compared with that pulse taken out.
    """
    from qccd.ir.tsir import iter_operands

    for instr in prog.instructions:
        if instr.type in ("measure", "reset"):
            for ion in instr.ions:
                yield instr.type, (qubit_of.get(ion),), ()
            continue
        if instr.type != "gate":
            continue
        if instr.gate not in NATIVE:
            raise NotImplementedError(f"gate {instr.gate!r} is not a native pulse {NATIVE}")
        width = 2 if instr.gate == "MS" else 1
        params = list(instr.params)
        for k, operand in enumerate(iter_operands(instr)):
            if len(operand) != width:
                raise NotImplementedError(
                    f"instruction {instr.id}: {instr.gate} on {len(operand)} ion(s)")
            qs = []
            for ion in operand:
                if ion not in qubit_of:
                    raise NotImplementedError(
                        f"instruction {instr.id}: {instr.gate} on ion {ion}, which carries no qubit")
                qs.append(qubit_of[ion])
            yield instr.gate, tuple(qs), tuple(params[k]) if k < len(params) else _NO_PARAMS[instr.gate]


def tableau_from_program(prog: TSIR, cert: dict, n: int, anc: dict | None = None):
    """Compose the EMITTED pulses into a stabilizer tableau over the logical qubits.

    Reads the hardware program, not the circuit and not the compiler's intent.  With
    `anc` (`readout_plan`), each measurement and reset becomes the unitary that defers it
    onto its own fresh qubit, so where it happens is compared too; without, they are
    skipped, as `qccd/bench/check.py` expects.
    """
    import stim

    qubit_of = {ion: int(q) for q, ion in cert["map"].items()}
    circ = stim.Circuit()
    circ.append("I", list(range(n + len(anc or {}))))
    seen: dict = {}
    for gate, qs, p in program_steps(prog, qubit_of):
        if gate in ("measure", "reset"):
            if anc is not None:
                _defer(circ.append, gate, qs[0], seen, anc)
        elif gate == "R":
            th, ph = p
            if not (is_right_angle(th) and is_right_angle(ph)):
                raise NotImplementedError(f"non-Clifford pulse R({th},{ph})")
            _emit_r(circ, qs[0], th, ph)
        elif gate == "VZ":
            (lam,) = p
            if not is_right_angle(lam):
                raise NotImplementedError(f"non-Clifford frame VZ({lam})")
            # VZ(lam) = diag(1, e^{i lam}) -- S applied `lam / (pi/2)` times
            for _ in range(round(lam / (PI / 2)) % 4):
                circ.append("S", [qs[0]])
        else:
            (th,) = p
            if not is_right_angle(th):
                raise NotImplementedError(f"non-Clifford MS({th})")
            _emit_ms(circ, qs[0], qs[1], th)
    return stim.Tableau.from_circuit(circ)


def _defer(emit, kind: str, q: int | None, seen: dict, anc: dict) -> None:
    """The deferred form of the `j`-th measurement or reset of qubit `q`: a CNOT onto its
    fresh qubit (a Z measurement IS that CNOT, read out at the end) or a SWAP with it (a
    reset hands the old state to a qubit nobody reads and takes a fresh |0>).  A last
    measurement on both sides is absent from `anc`: deferring it is exact on each side
    alone, so it drops out of the comparison."""
    j = seen.get(q, 0)
    seen[q] = j + 1
    a = anc.get((q, j))
    if a is not None:
        emit("CX" if kind == "measure" else "SWAP", [q, a])


def _emit_r(circ, q: int, theta: float, phi: float) -> None:
    """R(theta, phi) = exp(-i theta/2 (cos phi X + sin phi Y)), for theta a multiple of pi/2."""
    k = round(theta / (PI / 2)) % 4          # quarter turns
    axis = round(phi / (PI / 2)) % 4         # 0 = X, 1 = Y, 2 = -X, 3 = -Y
    if k == 0:
        return
    # rotate about +X or +Y; a negative axis is the same axis with the opposite sense
    if axis in (0, 2):
        base, sense = "X", (1 if axis == 0 else -1)
    else:
        base, sense = "Y", (1 if axis == 1 else -1)
    turns = (k * sense) % 4
    if turns == 0:
        return
    # stim's SQRT_X is exp(-i pi/4 X) up to a global phase, i.e. R(+pi/2, 0) -- NOT its
    # dagger.  Getting this backwards is invisible on a single gate (a tableau ignores
    # global phase) and shows up only as a whole-program mismatch, which is what this
    # checker is for.
    if turns == 2:
        circ.append(base, [q])               # a pi rotation is the Pauli itself
    elif turns == 1:
        circ.append("SQRT_X" if base == "X" else "SQRT_Y", [q])
    else:
        circ.append("SQRT_X_DAG" if base == "X" else "SQRT_Y_DAG", [q])


def _emit_ms(circ, a: int, b: int, theta: float) -> None:
    """MS(theta) = exp(-i theta/2 X⊗X), for theta a multiple of pi/2."""
    k = round(theta / (PI / 2)) % 4
    if k == 0:
        return
    if k == 2:
        circ.append("X", [a])
        circ.append("X", [b])
        return
    circ.append("SQRT_XX" if k == 1 else "SQRT_XX_DAG", [a, b])


def program_unitary(prog: TSIR, cert: dict, n: int, anc: dict | None = None):
    """Compose the EMITTED pulses into the exact unitary over the logical qubits, through
    the same reading of the program and the same qubit->ion map as `tableau_from_program`;
    with `anc`, over `n + len(anc)` qubits, each measurement and reset deferred."""
    import numpy as np

    U = _unitary()
    qubit_of = {ion: int(q) for q, ion in cert["map"].items()}
    width = n + len(anc or {})
    M = np.eye(1 << width, dtype=complex)

    def apply(name, qs):
        nonlocal M
        M = U.apply_gate(M, U.gate_matrix(name.lower(), [])[0], list(qs), width)

    seen: dict = {}
    for gate, qs, p in program_steps(prog, qubit_of):
        if gate in ("measure", "reset"):
            if anc is not None:
                _defer(apply, gate, qs[0], seen, anc)
        elif gate == "R":
            M = U.apply_gate(M, U.r_pulse(*p), list(qs), width)
        elif gate == "VZ":
            M = U.apply_gate(M, U.vz(*p), list(qs), width)
        else:
            M = U.apply_gate(M, U.ms(*p), list(qs), width)
    return M


# ------------------------------------------------------------------ the source circuit


def load_source(qasm: Path):
    """The source circuit, parsed by qiskit: the ONE reading of the QASM that both semantic
    routes and `check_binding` use."""
    from qiskit import qasm2

    return qasm2.load(str(qasm), custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)


def source_ops(qc) -> list[dict]:
    """The source circuit's operations in program order, measure/reset/barrier included.

    Qubits are numbered `qc.find_bit(q).index` -- declaration order, registers in the order
    their `qreg`s appear -- which is the numbering the compiler gives them and the
    certificate's qubit->ion map is written in.  A classically conditioned op
    (`if (c == v) g ...;`) is unwrapped into its body and flagged `cond`: neither semantic
    route can compare it, and the certificate does not record the condition.
    """
    out: list[dict] = []
    for inst in qc.data:
        op = inst.operation
        qs = [qc.find_bit(q).index for q in inst.qubits]
        if op.name == "if_else":
            body = op.blocks[0]
            for b in body.data:
                out.append(_source_op(b.operation, [qs[body.find_bit(q).index] for q in b.qubits],
                                      cond=True))
            continue
        out.append(_source_op(op, qs, cond=False))
    return out


def _source_op(op, qubits: list[int], cond: bool) -> dict:
    params = []
    for p in op.params:
        try:
            params.append(float(p))
        except (TypeError, ValueError):
            params.append(p)
    return {"name": op.name, "qubits": qubits, "params": params, "cond": cond, "op": op}


def source_matrix(o: dict, cache: dict | None = None):
    """The defining unitary of one SOURCE op, in `unitary.py`'s bit order (the op's first
    qubit is the most significant).

    Read from qiskit's own definition of the gate it parsed -- a qelib1 gate, or the body of
    a `gate` the QASM declares -- never from the compiler's lowering: a `ccx` enters as the
    Toffoli, not as the CNOTs the compiler turned it into.  qiskit orders an operator's
    qubits the other way round (its first qubit least significant), hence `reverse_qargs`;
    `tests/test_check_cert_source.py` pins that against `unitary.gate_matrix`.
    """
    import numpy as np
    from qiskit.quantum_info import Operator

    key = (o["name"], tuple(map(str, o["params"])), len(o["qubits"]))
    if cache is not None and key in cache:
        return cache[key]
    try:
        m = np.asarray(Operator(o["op"]).reverse_qargs().data, dtype=complex)
    except Exception as exc:  # noqa: BLE001 -- qiskit has no matrix for it: say so
        raise NotImplementedError(f"no defining unitary for source gate {o['name']}: {exc}") from None
    if cache is not None:
        cache[key] = m
    return m


def source_unitary(ops: list[dict], n: int, anc: dict | None = None):
    """The SOURCE circuit's exact unitary: every unitary op of the QASM at its defining
    matrix, composites included; barriers skipped; measure/reset deferred onto the qubits
    `anc` gives them (`readout_plan`), or skipped without it, as on the program side."""
    import numpy as np

    U = _unitary()
    width = n + len(anc or {})
    M = np.eye(1 << width, dtype=complex)
    cache: dict = {}

    def apply(name, qs):
        nonlocal M
        M = U.apply_gate(M, U.gate_matrix(name.lower(), [])[0], list(qs), width)

    seen: dict = {}
    for o in ops:
        if o["name"] in NON_UNITARY:
            if anc is not None and o["name"] != "barrier":
                _defer(apply, o["name"], o["qubits"][0], seen, anc)
            continue
        if o["cond"]:
            raise NotImplementedError(f"classically conditioned source gate {o['name']}")
        M = U.apply_gate(M, source_matrix(o, cache), o["qubits"], width)
    return M


def circuit_tableau(qasm: Path, n: int, ops: list[dict] | None = None, anc: dict | None = None):
    """The source circuit's tableau, via qiskit -> stim, for the Clifford fragment; with
    `anc`, each measurement and reset deferred as on the program side."""
    import stim

    if ops is None:
        ops = source_ops(load_source(qasm))
    circ = stim.Circuit()
    circ.append("I", list(range(n + len(anc or {}))))
    simple = {"x": "X", "y": "Y", "z": "Z", "h": "H", "s": "S", "sdg": "S_DAG",
              "sx": "SQRT_X", "sxdg": "SQRT_X_DAG", "id": "I",
              "cx": "CX", "cy": "CY", "cz": "CZ", "swap": "SWAP"}
    seen: dict = {}
    for o in ops:
        name, qs = o["name"], o["qubits"]
        if name in NON_UNITARY:
            if anc is not None and name != "barrier":
                _defer(circ.append, name, qs[0], seen, anc)
            continue
        if o["cond"]:
            raise NotImplementedError(f"classically conditioned source gate {name}")
        if name in simple:
            circ.append(simple[name], qs)
        elif name in ("rz", "u1", "p") and is_right_angle(float(o["params"][0])):
            k = round(float(o["params"][0]) / (PI / 2)) % 4
            for _ in range(k):
                circ.append("S", qs)
        else:
            raise NotImplementedError(f"non-Clifford source gate {name}")
    return stim.Tableau.from_circuit(circ)


# ------------------------------------------------------------------ measurements and resets


def readout_plan(src: list[dict], steps: list, n: int) -> tuple[dict, list[str]]:
    """Pair the circuit's measurements and resets with the program's, qubit by qubit.

    Both semantic routes compare unitaries, and a measurement or reset is not one -- so
    they used to be skipped, and a program that measured the wrong ion, measured before a
    gate instead of after it, or measured or reset a qubit the circuit does not was
    accepted.  Each is now compared in its deferred form (`_defer`): the `j`-th
    measurement of qubit `q` is a CNOT onto a fresh qubit of its own, the `j`-th reset a
    SWAP with one, on BOTH sides, so the two extended unitaries agree exactly when the
    readouts happen at equivalent points.  Two readouts that commute with what lies
    between them stay equal (a Z measurement before or after a CNOT it controls), as they
    should.

    Returns `(anc, problems)`: `anc` maps `(q, j)` to that event's fresh qubit; a
    measurement that is the last thing to happen to its qubit on both sides is left out,
    since deferring it is exact on each side alone.  `problems` is non-empty when the
    sequences of measurements and resets per qubit already differ.
    """
    def events(seq):
        ev: dict[int, list] = {}
        for kind, qs in seq:
            for q in qs:
                if ev.get(q):
                    ev[q][-1][1] = False         # something happens to q after its last readout
            if kind in ("measure", "reset"):
                ev.setdefault(qs[0], []).append([kind, True])
        return ev

    def from_source():
        for o in src:
            if o["name"] == "barrier":
                continue
            if o["name"] in ("measure", "reset"):
                if o["cond"]:
                    raise NotImplementedError(f"classically conditioned {o['name']}")
                yield o["name"], tuple(o["qubits"])
            else:
                yield "gate", tuple(o["qubits"])

    a = events(from_source())
    b = events((g if g in ("measure", "reset") else "gate", qs) for g, qs, _ in steps)
    problems = []
    if None in b:
        problems.append(f"the program measures or resets {len(b[None])} time(s) an ion that "
                        "carries no qubit")
    for q in sorted(k for k in set(a) | set(b) if k is not None):
        ka, kb = [k for k, _ in a.get(q, [])], [k for k, _ in b.get(q, [])]
        if ka != kb:
            problems.append(f"qubit {q}: the circuit {_story(ka)}, the program {_story(kb)}")
    anc: dict = {}
    if not problems:
        for q in sorted(a):
            for j, ((kind, last_a), (_, last_b)) in enumerate(zip(a[q], b[q])):
                if not (kind == "measure" and last_a and last_b):
                    anc[(q, j)] = n + len(anc)
    return anc, problems


def _story(kinds: list[str]) -> str:
    if not kinds:
        return "never measures or resets it"
    return "then ".join(f"{'measures' if k == 'measure' else 'resets'} it " for k in kinds).strip()


# ------------------------------------------------------------------ the binding

#: OpenQASM 2's two built-ins, which qiskit reads as the qelib1 gates of the same meaning
_ALIAS = {"U": "u", "CX": "cx"}
#: the longest run of circuit_ops one source gate may be lowered to
MAX_LOWERING = 4096


def check_binding(cert: dict, qc, src: list[dict]) -> list[str]:
    """Does the certificate describe THIS circuit?  The problems, empty if it does.

    The witnesses -- and so O1 and the Lean checker's verdict -- are about `circuit_ops`,
    the certificate's own copy of the circuit, and the semantic half reads the qubit->ion
    map.  Both are checked against the QASM here, never believed:

    * the certificate's `n_qubits` is the QASM's, and its map sends each of those qubits
      to its own ion;
    * `circuit_ops` is numbered 0..N-1, and is the source circuit op by op: an op on one
      or two qubits (and every measure, reset and barrier) appears as itself, on the same
      qubits in the same order, with the same name and angles or the same defining
      unitary; an op on three or more qubits appears as a run of ops on its qubits whose
      product is its defining unitary up to a global phase -- which is how the lowering of
      `ccx` and `cswap` is checked where it is used.

    One small matrix per op at most, so this runs at every size, Clifford or not.
    """
    problems: list[str] = []
    n = cert.get("n_qubits")
    if qc.num_qubits != n:
        problems.append(f"the QASM declares {qc.num_qubits} qubits, the certificate {n}")
    qmap = cert.get("map", {})
    if sorted(qmap) != sorted(str(q) for q in range(qc.num_qubits)):
        problems.append(f"map covers qubits {sorted(qmap, key=lambda k: (len(k), k))[:8]}, "
                        f"not 0..{qc.num_qubits - 1}")
    if len(set(qmap.values())) != len(qmap):
        problems.append("map sends two qubits to one ion")
    cops = cert.get("circuit_ops", [])
    if [o.get("i") for o in cops] != list(range(len(cops))):
        problems.append("circuit_ops is not numbered 0..N-1 in order")
    return problems + _match_ops(src, cops)


def _match_ops(src: list[dict], cops: list[dict]) -> list[str]:
    problems: list[str] = []
    cache: dict = {}
    j = 0
    for k, s in enumerate(src):
        where = f"source op {k} `{_show(s)}`"
        if j >= len(cops):
            problems.append(f"{where}: circuit_ops ends after {len(cops)} ops")
            return problems
        if s["name"] in NON_UNITARY or len(s["qubits"]) <= 2:
            why = _same_op(s, cops[j], cache)
            if why:
                problems.append(f"{where} vs circuit_ops[{j}] `{_show(cops[j])}`: {why}")
                if len(problems) >= 6:
                    return problems
            j += 1
        else:
            end, why = _lowered_run(s, cops, j, cache)
            if why:
                problems.append(f"{where} vs circuit_ops[{j}..]: {why}")
                return problems          # where its lowering ends is unknown: stop here
            j = end
    if j < len(cops):
        problems.append(f"circuit_ops has {len(cops) - j} op(s) beyond the source circuit, "
                        f"from circuit_ops[{j}] `{_show(cops[j])}`")
    return problems


def _show(o: dict) -> str:
    ps = o.get("params") or []
    args = "(" + ",".join(f"{float(p):.6g}" if isinstance(p, (int, float)) else str(p)
                          for p in ps) + ")" if ps else ""
    return f"{o['name']}{args} {list(o['qubits'])}"


def _cert_matrix(c: dict):
    got = _unitary().gate_matrix(_ALIAS.get(c["name"], c["name"]), [float(p) for p in c.get("params", [])])
    if got is None or got[1] != len(c["qubits"]):
        return None
    return got[0]


def _same_op(s: dict, c: dict, cache: dict) -> str | None:
    """Why the certificate's op `c` is not the source op `s`, or None if it is."""
    sname, cname = _ALIAS.get(s["name"], s["name"]), _ALIAS.get(c["name"], c["name"])
    if sname in NON_UNITARY or cname in NON_UNITARY:
        if sname != cname:
            return f"it is a {c['name']}, the source a {s['name']}"
        if sname == "barrier":
            return None if sorted(c["qubits"]) == sorted(s["qubits"]) else "a fence over other qubits"
        return None if list(c["qubits"]) == s["qubits"] else "on other qubits"
    if list(c["qubits"]) != s["qubits"]:
        return "on other qubits, or in another order"
    cp = [float(p) for p in c.get("params", [])]
    if (sname == cname and len(cp) == len(s["params"])
            and all(isinstance(b, float) and abs(a - b) <= 1e-9 for a, b in zip(cp, s["params"]))):
        return None
    mc = _cert_matrix(c)
    if mc is None:
        return "another gate, and one with no defining unitary to compare"
    try:
        ms = source_matrix(s, cache)
    except NotImplementedError as exc:
        return f"another gate name, and {exc}"
    if mc.shape != ms.shape or not _unitary().same_up_to_phase(mc, ms)[0]:
        return "another gate"
    return None


def _lowered_run(s: dict, cops: list[dict], j: int, cache: dict) -> tuple[int, str | None]:
    """The shortest run `cops[j:end]` on the qubits of `s` whose product is `s`'s defining
    unitary up to a global phase: (end, None), or (j, why there is none)."""
    import numpy as np

    U = _unitary()
    try:
        want = source_matrix(s, cache)
    except NotImplementedError as exc:
        return j, f"its lowering cannot be checked: {exc}"
    pos = {q: t for t, q in enumerate(s["qubits"])}
    k = len(s["qubits"])
    M = np.eye(1 << k, dtype=complex)
    for m in range(j, min(len(cops), j + MAX_LOWERING)):
        c = cops[m]
        if c["name"] in NON_UNITARY or any(q not in pos for q in c["qubits"]):
            break
        g = _cert_matrix(c)
        if g is None:
            return j, f"circuit_ops[{m}] `{_show(c)}` has no defining unitary to compose"
        M = U.apply_gate(M, g, [pos[q] for q in c["qubits"]], k)
        if U.same_up_to_phase(M, want)[0]:
            return m + 1, None
    return j, "no run of ops on its qubits composes to it: the lowering is not the gate"


# ------------------------------------------------------------------ driver


def certificate_shape(cert) -> list[str]:
    """What is malformed about a certificate, field by field: every field the checks below
    read, with the type they read it as.  A certificate with a problem here is refused with
    the problem named -- never half-read, never a crash."""
    out: list[str] = []

    def is_int(v):
        return isinstance(v, int) and not isinstance(v, bool)

    if not isinstance(cert, dict):
        return ["the certificate is not a JSON object"]
    if not is_int(cert.get("n_qubits")):
        out.append(f"n_qubits is {cert.get('n_qubits')!r}, not a whole number")
    if not isinstance(cert.get("map"), dict):
        out.append("map is missing or not an object")
    for key in ("moves", "gates", "circuit_ops"):
        if not isinstance(cert.get(key), list):
            out.append(f"{key} is missing or not a list")
    for key in ("rotations", "unrealised"):
        if key in cert and not isinstance(cert[key], list):
            out.append(f"{key} is not a list")
    for i, m in enumerate(cert.get("moves") or []) if isinstance(cert.get("moves"), list) else ():
        if not isinstance(m, dict):
            out.append(f"move {i} is not an object")
            continue
        if not is_int(m.get("t")):
            out.append(f"move {i} has t = {m.get('t')!r}, not a cycle number")
        for k in ("ion", "from", "to"):
            if not isinstance(m.get(k), str):
                out.append(f"move {i} has {k} = {m.get(k)!r}, not a name")
    for i, g in enumerate(cert.get("gates") or []) if isinstance(cert.get("gates"), list) else ():
        if not isinstance(g, dict):
            out.append(f"gate witness {i} is not an object")
            continue
        for k in ("t", "dag"):
            if not is_int(g.get(k)):
                out.append(f"gate witness {i} has {k} = {g.get(k)!r}, not a whole number")
        if "instr" in g and not is_int(g["instr"]):
            out.append(f"gate witness {i} has instr = {g['instr']!r}, not an instruction id")
        if not isinstance(g.get("ions"), list):
            out.append(f"gate witness {i} has no list of ions")
        if not isinstance(g.get("site"), str):
            out.append(f"gate witness {i} has site = {g.get('site')!r}, not a name")
    for i, r in enumerate(cert.get("rotations") or []) if isinstance(cert.get("rotations"), list) else ():
        if not isinstance(r, dict) or not is_int(r.get("t")) or not is_int(r.get("delta")):
            out.append(f"rotation {i} needs a whole-number t and delta")
    return out[:20]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("prefix", help="the compiler's -o prefix (.tsir.json / .qcert.json)")
    ap.add_argument("--qasm", required=True)
    ap.add_argument("--arch", required=True)
    ap.add_argument("--qcheck", default=None,
                    help="a qcheck input from mk_qcheck_input.py; runs the PROVED Lean "
                         "checker and lets R10 be reported as `passed`")
    ap.add_argument("--max-unitary-qubits", type=int, default=10,
                    help="fall back to an exact unitary comparison up to this size; "
                         "2^n x 2^n, so 10 is ~16 MB")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    cert = json.loads(Path(args.prefix + ".qcert.json").read_text(encoding="utf-8"))
    prog = TSIR.load(args.prefix + ".tsir.json")
    # a relative --arch is read from where the command runs; the repository root is kept
    # as the fallback older callers relied on
    arch_path = Path(args.arch)
    if not arch_path.is_absolute() and not arch_path.exists():
        arch_path = ROOT / args.arch
    arch = Architecture.from_json(json.loads(arch_path.read_text(encoding="utf-8")))

    verdict: dict = {"program": prog.name, "arch": arch.name}
    shape = certificate_shape(cert)
    if shape:
        verdict.update({"shape": shape, "binding": "not checked", "transport": "not checked",
                        "o1_transport": [f"malformed certificate: {x}" for x in shape[:6]],
                        "order": "not checked", "o2_semantics": "not checked",
                        "method": "none: the certificate is malformed",
                        "unrealised_ops": 0, "R10": "FAILED",
                        "R10_reason": f"the certificate is malformed: {shape[0]}"})
        if args.json:
            print(json.dumps(verdict, indent=1))
        else:
            print(f"R10 for {prog.name} on {arch.name}")
            for x in shape:
                print(f"  malformed: {x}")
            print(f"  -> R10 FAILED: {verdict['R10_reason']}")
        return 1

    # the binding: a certificate with no binding is a certificate for anything
    qasm_sha = hashlib.sha256(Path(args.qasm).read_bytes()).hexdigest()
    verdict["circuit_sha256"] = qasm_sha

    # ...and the certificate's own copy of the circuit, which the witnesses index, must be
    # that circuit.  A disagreement is reported under O1 as well: witnesses for another
    # circuit's ops say nothing about this one's, and callers that read only
    # `o1_transport` (the workspace evaluator) must see it.
    qc = load_source(Path(args.qasm))
    src = source_ops(qc)
    binding = check_binding(cert, qc, src)
    verdict["binding"] = "ok" if not binding else binding[:6]

    # the certificate's moves are the programme's own: what O1 proves of the one holds of the other
    transport = check_program_transport(cert, prog)
    verdict["transport"] = "ok" if not transport else transport[:6]
    o1 = check_o1(cert, arch) + check_witnesses(cert, prog) + [f"programme vs certificate: {x}" for x in transport]
    o1_all = [f"certificate vs QASM: {b}" for b in binding] + o1
    verdict["o1_transport"] = "ok" if not o1_all else o1_all[:6]

    # each qubit's ops in the circuit's own order, commuting or not
    order = check_program_order(cert, prog)
    verdict["order"] = "ok" if not order else order[:6]

    # The proved checker.  Until C6 this did not exist and R10 was capped at `partial`
    # by decision D3; now that it does, its verdict is what lifts the cap.
    lean_ok: bool | None = None
    unbound: list[str] = []
    if args.qcheck:
        exe = find_qcheck(ROOT / "Compiler" / "lean")
        unbound = qcheck_binding(Path(args.qcheck), cert, arch)
        if unbound:
            # its verdict would be about some other certificate: not run, not believed
            lean_ok = False
            verdict["lean"] = "REFUSED: not this certificate's input"
            verdict["lean_why"] = unbound
        elif exe is None:
            verdict["lean"] = "qcheck not built"
        else:
            r = subprocess.run([str(exe), args.qcheck], capture_output=True, text=True)
            lean_ok = "ACCEPTED" in r.stdout
            verdict["lean"] = "accepted" if lean_ok else "REJECTED"
            if not lean_ok:
                verdict["lean_why"] = [
                    ln.strip()[2:] for ln in r.stdout.splitlines()
                    if ln.strip().startswith("- ")]

    unrealised = cert.get("unrealised", [])
    verdict["unrealised_ops"] = len(unrealised)

    n = cert["n_qubits"]
    same: bool | None = None
    anc: dict | None = None
    readout: list[str] | None = None
    if qc.num_qubits != n or any(b.startswith("map ") for b in binding):
        # the pulses cannot be read over the circuit's qubits; R10 fails on the binding
        verdict["o2_semantics"] = "not checked: the certificate's qubits are not the QASM's"
        verdict["method"] = "none"
    else:
        try:
            qubit_of = {ion: int(q) for q, ion in cert["map"].items()}
            anc, readout = readout_plan(src, list(program_steps(prog, qubit_of)), n)
        except NotImplementedError as exc:
            anc, readout = None, None
            verdict["o2_semantics"] = f"not checked: {exc}"
            verdict["method"] = "none: the program cannot be read exactly"
        if readout:
            same = False
            verdict["readout"] = readout[:6]
            verdict["o2_semantics"] = f"READOUT MISMATCH: {readout[0]}"
            verdict["method"] = "measurements and resets, qubit by qubit, against the source QASM"
        elif anc is not None:
            verdict["readout"] = (f"ok: {len(anc)} measurement(s)/reset(s) compared in place, "
                                  "final measurements at the end")
    if anc is not None and not readout:
        try:
            got = tableau_from_program(prog, cert, n, anc)
            want = circuit_tableau(Path(args.qasm), n, src, anc)
            same = got == want
            verdict["o2_semantics"] = "ok" if same else "TABLEAU MISMATCH"
            verdict["method"] = ("stabilizer tableau, composed from the emitted pulses, "
                                 "against the source QASM")
        except NotImplementedError as exc:
            # Outside the Clifford fragment the tableau says nothing.  For a circuit small
            # enough to hold a full operator, compare the unitaries instead -- which is a
            # STRONGER check (it sees global phase and every amplitude), just one that costs
            # 2^n x 2^n and so cannot scale.  The reference is the SOURCE circuit, read from
            # the QASM with its composites at their defining matrices -- never the
            # certificate's `circuit_ops`, which is the compiler's own lowering of it: a
            # comparison against that would pass a `ccx` compiled from `t q[0];`.
            same = None
            verdict["o2_semantics"] = f"not checked: {exc}"
            verdict["method"] = "outside the Clifford fragment (decision D1)"
            width = n + len(anc)                 # the qubits, and one per deferred readout
            if width > args.max_unitary_qubits:
                verdict["o2_semantics"] = (
                    f"not checked: {exc}; {n} qubits and {len(anc)} mid-circuit "
                    f"measurement(s)/reset(s) exceed --max-unitary-qubits {args.max_unitary_qubits}")
            else:
                try:
                    U = _unitary()
                    a = program_unitary(prog, cert, n, anc)
                    b = source_unitary(src, n, anc)
                    ok_u, alpha, worst = U.same_up_to_phase(a, b)
                    same = ok_u
                    verdict["o2_semantics"] = (
                        f"ok (max entry error {worst:.2e})" if ok_u
                        else f"UNITARY MISMATCH (max entry error {worst:.2e})")
                    verdict["method"] = (
                        f"exact {1 << width}x{1 << width} unitary, built from the emitted pulses, "
                        f"against the source QASM; global phase {alpha:+.4f}")
                except NotImplementedError as exc2:
                    verdict["o2_semantics"] = f"not checked: {exc2}"
                except MemoryError:
                    verdict["o2_semantics"] = f"not checked: {width} qubits is too large"

    structural_ok = (not o1) and not unrealised
    if unbound:
        verdict["R10"] = "FAILED"
        verdict["R10_reason"] = (
            "the qcheck input was not made from this certificate on this device, so the "
            "Lean checker's verdict on it is not this certificate's")
    elif lean_ok is False:
        verdict["R10"] = "FAILED"
        verdict["R10_reason"] = "the proved Lean checker rejected the certificate"
    elif binding:
        verdict["R10"] = "FAILED"
        verdict["R10_reason"] = (
            "the certificate does not describe the source circuit: its circuit_ops, qubit "
            "count or qubit map disagree with the QASM, so its witnesses are not this "
            "circuit's")
    elif not structural_ok:
        verdict["R10"] = "FAILED"
        verdict["R10_reason"] = ("transport, co-location, coverage or the witnesses' "
                                 "instructions did not check out")
    elif order:
        verdict["R10"] = "FAILED"
        verdict["R10_reason"] = ("a qubit runs its ops out of the circuit's order; the "
                                 "compiler may not reorder them, even where they commute")
    elif same is False:
        verdict["R10"] = "FAILED"
        verdict["R10_reason"] = "the compiled program does not implement the circuit"
    elif same is True and lean_ok:
        # Both halves, and the O1 half by a checker proved sound in Lean
        # (`QCCDC.Cert.check_sound`).  This is the condition D3 set for `passed`.
        verdict["R10"] = "passed"
        verdict["R10_reason"] = (
            "O1 by the proved Lean checker (QCCDC.Cert.check_sound); O2 by "
            + verdict["method"])
    elif same is True:
        verdict["R10"] = "partial"
        verdict["R10_reason"] = (
            "O1 and O2 both check out, but only the fast OCaml/Python checker ran; "
            "pass --qcheck to have the proved Lean checker decide it")
    else:
        # Outside the Clifford fragment the stabilizer route says nothing, and saying
        # nothing is not the same as failing.  D1 defers these to the symbolic route.
        # Even with the Lean checker accepting, O2 is unestablished, so this stays
        # `partial` -- O1 alone is not R10.
        verdict["R10"] = "partial"
        verdict["R10_reason"] = (
            "O1 checks out; O2 not established -- the circuit is outside the Clifford "
            "fragment, where decision D1 defers to the symbolic route")
    ok = verdict["R10"] in ("partial", "passed")

    if args.json:
        print(json.dumps(verdict, indent=1))
    else:
        print(f"R10 for {prog.name} on {arch.name}")
        print(f"  certificate vs QASM        : {verdict['binding']}")
        print(f"  O1 transport + co-location : {verdict['o1_transport']}")
        print(f"  O2 semantics               : {verdict['o2_semantics']}")
        if "readout" in verdict:
            print(f"  measurements and resets    : {verdict['readout']}")
        if "lean" in verdict:
            print(f"  proved Lean checker        : {verdict['lean']}")
        print(f"     ({verdict['method']})")
        if unrealised:
            print(f"  UNREALISED circuit ops     : {len(unrealised)} {unrealised[:8]}")
        print(f"  -> R10 {verdict['R10']}: {verdict['R10_reason']}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
