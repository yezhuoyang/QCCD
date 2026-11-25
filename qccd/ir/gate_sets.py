"""The native gate sets a compiler lowers a circuit to.

A trapped-ion machine drives every ion with one-qubit pulses and every pair with ONE entangling
interaction; which interaction is the machine's choice, and a compiler option here (`gate_set`):

    "ms"   R(theta, phi), VZ(lambda), MS(theta)  = exp(-i theta/2 X(x)X)   the default
    "rzz"  R(theta, phi), VZ(lambda), RZZ(theta) = exp(-i theta/2 Z(x)Z)   Quantinuum's native set
                                                                            (H-series and Helios:
                                                                            arXiv:2003.01293,
                                                                            arXiv:2511.05465)

`R` is the one-qubit pulse exp(-i theta/2 (cos phi X + sin phi Y)) (Quantinuum's U1q) and `VZ`
the frame change diag(1, e^{i lambda}), the same in both sets.  The two sets differ only in the
entangling pulse, and so in the one-qubit pulses a circuit gate is dressed with: a CX is one
entangling pulse in either, with four one-qubit pulses around an MS and two around an RZZ; a CZ
needs none around an RZZ (`qccd.compile.c2lr_router.pulses`, `Compiler/ocaml/lib/gateset.ml`).

Either entangling pulse is the machine's native two-qubit primitive: it is priced as `ms_gate`
and noised by the `ms*` channels (`qccd.cost.models`, the `.eval` noise texts), which name the
primitive, not the axis.  A programme may hold pulses of one set only when it was compiled, but
nothing that reads a programme (the replay, the rules, the stim translation, `check_cert.py`)
asks which set it is in: every pulse of `ALL_NATIVE` is read by its name.

A frame change is priced by the machine's physics, as it always was (`CostModel.gate_1q`): no
time where the machine declares a free one (`z_gate` at 0 us: the Helios nominal physics,
qccd-physics@3), and three one-qubit pulses where it declares none.  The rzz forms lean on frame
changes -- two for a CX, where the ms form has none -- so in one-qubit pulse times a CX costs

    ms    4                  rzz    2 with a free frame change, 8 without

and a CZ 6 (ms; 12 without a free frame change) against 0 (rzz; 6 without).  On a machine with
no free frame change an rzz CX is therefore the slower one, by four one-qubit pulses, and under
a noise text that charges a frame change as pulses (qccd-noise@2) the noisier one.
"""

from __future__ import annotations

__all__ = ["GATE_SETS", "DEFAULT", "ENTANGLER", "ENTANGLERS", "NATIVE", "ALL_NATIVE", "KIND",
           "resolve", "of_program", "entanglers_used", "named", "derive", "label", "labels",
           "binary_gate_sets", "binary_refusal"]

#: the option's values, the default first
GATE_SETS = ("ms", "rzz")
DEFAULT = "ms"

#: each set's entangling pulse, as a TSIR gate name; its one parameter is the angle theta
ENTANGLER = {"ms": "MS", "rzz": "RZZ"}
#: every entangling pulse, of either set
ENTANGLERS = tuple(ENTANGLER[s] for s in GATE_SETS)
#: each set's pulses
NATIVE = {s: ("R", "VZ", ENTANGLER[s]) for s in GATE_SETS}
#: every native pulse a programme may hold
ALL_NATIVE = ("R", "VZ") + ENTANGLERS
#: how a compiler tags a pulse instruction (`meta.kind`)
KIND = {"R": "beam", "VZ": "virtual_z", "MS": "ms", "RZZ": "rzz"}


def resolve(gate_set: str | None) -> str:
    """The gate set named (None: the default), or ValueError naming the sets there are."""
    if gate_set is None:
        return DEFAULT
    key = str(gate_set).strip().lower()
    if key not in GATE_SETS:
        raise ValueError(f"unknown gate set {gate_set!r}: the sets are {', '.join(GATE_SETS)}")
    return key


_BINARY_SETS: dict = {}


def binary_gate_sets(exe) -> tuple[str, ...]:
    """The gate sets a `qccdc_cli` binary lowers to: what `qccdc_cli gate-sets` prints.  A binary
    built before the option existed has no such command -- and IGNORES `--gate-set`, compiling to
    MS whatever it is asked -- so it answers the default set alone."""
    import subprocess
    from pathlib import Path

    p = Path(exe)
    try:
        st = p.stat()
        key = (str(p), st.st_size, st.st_mtime)
    except OSError:
        return (DEFAULT,)
    if key not in _BINARY_SETS:
        have: tuple[str, ...] = (DEFAULT,)
        try:
            cp = subprocess.run([str(p), "gate-sets"], capture_output=True, text=True, timeout=60)
            names = tuple(w for w in cp.stdout.split() if w in GATE_SETS)
            if cp.returncode == 0 and DEFAULT in names:
                have = names
        except (OSError, subprocess.SubprocessError):
            pass
        _BINARY_SETS[key] = have
    return _BINARY_SETS[key]


def binary_refusal(exe, gate_set: str) -> str | None:
    """Why the `qccdc_cli` binary `exe` cannot compile to `gate_set`, with the way out; None if
    it can.  Asked BEFORE compiling: an old binary would compile to MS and say nothing."""
    gate_set = resolve(gate_set)
    have = binary_gate_sets(exe)
    if gate_set in have:
        return None
    return (f"the compiler at {exe} lowers to the gate set {', '.join(have)} only (it was built before "
            f"the gate-set option), so it cannot compile to gate_set={gate_set!r}.  Build the compiler from "
            f"this source (`cd Compiler/ocaml && dune build`: a build there is used before an installed "
            f"release; or name a binary with QCCD_QCCDC), or install a toolchain release whose "
            f"`qccdc_cli gate-sets` lists {gate_set}.  The Python routers (C2LR, WISE, Cyclone, Helios, "
            f"conveyor) take gate_set={gate_set!r} with no binary.")


def of_program(prog) -> str | None:
    """The gate set a programme's pulses are in: the one whose entangling pulse it uses, the
    default for a programme with no entangling pulse, None for one that mixes the two (or
    whose two-ion gates are not native pulses at all: an authored `CX`)."""
    used = {i.gate for i in prog.instructions if i.type == "gate" and i.gate in ENTANGLERS}
    if not used:
        return DEFAULT
    if len(used) > 1:
        return None
    return next(s for s in GATE_SETS if ENTANGLER[s] in used)


def entanglers_used(program) -> set:
    """The entangling pulses (`ENTANGLERS`) a programme -- a `TSIR` or its JSON document -- holds."""
    if hasattr(program, "instructions"):
        return {i.gate for i in program.instructions if i.type == "gate" and i.gate in ENTANGLERS}
    return {i.get("gate") for i in (program or {}).get("instructions") or ()
            if isinstance(i, dict) and i.get("type") == "gate" and i.get("gate") in ENTANGLERS}


def label(gate_set: str | None) -> str:
    """A gate set in the words every page, table, tooltip, zip and report uses: "MS (R, VZ, MS)",
    "RZZ (R, VZ, RZZ)" (None: the default).  The ONE spelling: a page's script takes its table from
    `labels()`, never its own."""
    gs = DEFAULT if gate_set is None else gate_set
    return f"{ENTANGLER[gs]} ({', '.join(NATIVE[gs])})" if gs in NATIVE else str(gs)


def labels() -> dict:
    """Every set's words, for a page's script: {set: {name, pulses, label}} -- "MS", "R, VZ, MS",
    "MS (R, VZ, MS)"."""
    return {s: {"name": ENTANGLER[s], "pulses": ", ".join(NATIVE[s]), "label": label(s)} for s in GATE_SETS}


def named(program=None, certificate=None) -> list:
    """What a programme's `meta` and a certificate's `claims` say their gate set is, in that order:
    a compiler writes `gate_set` there for a set that is not the default, and nothing for it."""
    out = []
    meta = (program.meta if hasattr(program, "meta") else (program or {}).get("meta")) if program is not None else None
    claims = (certificate or {}).get("claims") if isinstance(certificate, dict) else None
    for where in (meta, claims):
        if isinstance(where, dict) and where.get("gate_set") is not None:
            out.append(str(where["gate_set"]).strip().lower())
    return out


def derive(program, certificate=None) -> str | None:
    """The gate set a compiled programme is in, from the programme and its certificate: its
    entangling pulses decide, and where it has none what its `meta` or its certificate's `claims`
    name; the default when nothing says otherwise.  None when they disagree (a programme that
    mixes MS and RZZ, or whose pulses are not the set its compiler wrote down)."""
    used = entanglers_used(program)
    said = set(named(program, certificate))
    if len(used) > 1 or not said <= set(GATE_SETS) or len(said) > 1:
        return None
    if used:
        gs = next(s for s in GATE_SETS if ENTANGLER[s] in used)
        return gs if not said or said == {gs} else None
    return next(iter(said)) if said else DEFAULT
