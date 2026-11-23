"""The compiler's gate-set option: `gate_set="ms"` (the default) or `"rzz"` (Quantinuum's set).

`qccd/ir/gate_sets.py` names the two sets.  Held here:

* the lowering: every circuit gate's pulses, in either set, multiply out to the gate's defining
  unitary up to a global phase (`Compiler/bridge/unitary.py`), and the rzz forms are the ones
  with the fewest physical pulses -- a CX is one RZZ and two one-qubit pulses on its target (no
  CX is one RZZ and a single one-qubit pulse), a CZ one RZZ and none;
* the default set is what it was: the same pulse lists, no `gate_set` anywhere in a programme or
  a certificate, and leaving the option out is naming `ms`;
* end to end, in both sets, on the C2LR router, the WISE router, the Cyclone router and qccdc
  built from source (skipped when the build is absent or older than the option): a Bell pair, a
  GHZ state and a d = 3 repetition-code memory compile; the router's own rule set passes; the
  certificate checks hold (binding, transport, witnesses, per-qubit order, and the stabilizer
  tableau composed from the EMITTED pulses against the source circuit, readouts in place); the
  memory extracts to a noisy stim circuit under qccd-noise@1, @2 and @3, with every detector
  deterministic and the source's parity, and samples; and the two sets' programmes agree --
  the same tableau, the same transport, the same detectors and reference parities;
* a programme that holds `RZZ` prints and reads back in both text syntaxes, replays, and is
  priced as the machine's two-qubit primitive;
* a compiler binary from before the option is refused, by name and with the way out, instead of
  being run to compile MS; QCCDCert, which lowers to MS only, is not chosen for an rzz run.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

stim = pytest.importorskip("stim")
np = pytest.importorskip("numpy")

from qccd.api import Machine  # noqa: E402
from qccd.arch import Architecture, c2lr, cyclone, wise  # noqa: E402
from qccd.compile import c2lr_router, conveyor_router, cyclone_router, helios_router, wise_router  # noqa: E402
from qccd.compile.c2lr_router import GATES_2Q, U3, SourceOp, pulses  # noqa: E402
from qccd.cost.models import corrected_model  # noqa: E402
from qccd.ir import gate_sets  # noqa: E402
from qccd.ir.tsir import TSIR, Instruction  # noqa: E402
from qccd.qec import CompileFailed, compile_with_qccdc, repetition  # noqa: E402
from qccd.qec.check import noiseless_check  # noqa: E402
from qccd.qec.extract import extract  # noqa: E402
from qccd.ruleset import FAILS, check, legacy_config, load as load_rules  # noqa: E402
from qccd.ruleset.compile import circuit_of_certificate  # noqa: E402
from qccd.ruleset.programme import programme_execution  # noqa: E402
from qccd.verify import verify  # noqa: E402

PI = math.pi
BRIDGE = ROOT / "Compiler" / "bridge"
SETS = ("ms", "rzz")
TABLE = "helios"

BELL = 'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\ncreg c[2];\nh q[0];\ncx q[0],q[1];\n' \
       'measure q[0] -> c[0];\nmeasure q[1] -> c[1];\n'
#: a star, so that on a ring with one data qubit (q0) every gate is between it and an ancilla; the
#: CZ between two Hadamards is a CX written the other way, and is what the rzz set lowers to one pulse
GHZ = 'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[3];\ncreg c[3];\nh q[0];\ncx q[0],q[1];\n' \
      'h q[2];\ncz q[0],q[2];\nh q[2];\nmeasure q[0] -> c[0];\nmeasure q[1] -> c[1];\nmeasure q[2] -> c[2];\n'
MEMORY = repetition(3)
#: circuit -> (QASM, how many of its first qubits are data qubits, as the ring compilers ask)
CIRCUITS = {"bell": (BELL, 1), "ghz": (GHZ, 1), "rep3": (MEMORY.qasm, MEMORY.n_data)}


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"gate_set_{name}", BRIDGE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def U():
    return _load("unitary")


@pytest.fixture(scope="module")
def cc():
    return _load("check_cert")


# ------------------------------------------------------------------ the lowering


def _product(U, seq, ion) -> "np.ndarray":
    """The pulses of `seq`, multiplied out over the ions of `ion` (qubit 0 the most significant)."""
    at = {name: q for q, name in ion.items()}
    M = np.eye(1 << len(ion), dtype=complex)
    for gate, operands, params in seq:
        m = {"R": U.r_pulse, "VZ": U.vz, **U.ENTANGLING}[gate](*params)
        M = U.apply_gate(M, m, [at[x] for x in operands], len(ion))
    return M


@pytest.mark.parametrize("gate_set", SETS)
@pytest.mark.parametrize("name", sorted(U3) + list(GATES_2Q))
def test_every_gate_s_pulses_are_the_gate(U, gate_set, name):
    n = 2 if name in GATES_2Q else 1
    ion = {q: f"q{q}" for q in range(n)}
    seq = pulses(SourceOp(0, name, tuple(range(n)), 1), ion, gate_set)
    want = U.gate_matrix(name, [])[0]
    ok, _, worst = U.same_up_to_phase(_product(U, seq, ion), want)
    assert ok, (name, gate_set, worst)
    assert {g for g, _, _ in seq} <= set(gate_sets.NATIVE[gate_set])
    if n == 2:
        # the operands the other way round are the gate the other way round, not the same one
        other = pulses(SourceOp(0, name, (1, 0), 1), ion, gate_set)
        flipped = U.apply_gate(U.apply_gate(np.eye(4, dtype=complex), U.SWAP, [0, 1], 2), want, [0, 1], 2)
        flipped = U.apply_gate(flipped, U.SWAP, [0, 1], 2)
        assert U.same_up_to_phase(_product(U, other, ion), flipped)[0]


def test_the_entangling_pulses_are_the_rotations_they_name(U):
    for theta in (PI / 2, -PI / 2, PI, 0.37):
        assert np.allclose(U.rzz(theta), U.gate_matrix("rzz", [theta])[0])
        assert np.allclose(U.ms(theta), U.gate_matrix("rxx", [theta])[0])
    assert not U.same_up_to_phase(U.rzz(PI / 2), U.ms(PI / 2))[0]


def _counts(seq) -> dict:
    out: dict = {}
    for g, _, _ in seq:
        out[g] = out.get(g, 0) + 1
    return out


def test_the_pulse_counts_of_the_two_sets():
    ion = {0: "c", 1: "t"}
    cx, cz = SourceOp(0, "cx", (0, 1), 1), SourceOp(1, "cz", (0, 1), 1)
    assert _counts(pulses(cx, ion, "ms")) == {"R": 4, "MS": 1}
    assert _counts(pulses(cz, ion, "ms")) == {"R": 6, "VZ": 2, "MS": 1}
    assert _counts(pulses(cx, ion, "rzz")) == {"R": 2, "RZZ": 1, "VZ": 2}
    assert _counts(pulses(cz, ion, "rzz")) == {"RZZ": 1, "VZ": 2}
    # the rzz CX drives the target only: its control takes a frame change and no laser of its own
    assert {o for g, o, _ in pulses(cx, ion, "rzz") if g == "R"} == {("t",)}
    # and the forms are these, angle for angle, in time order (the CNOT as Quantinuum documents it
    # for the H-series: a pulse on the target either side of the ZZ, a frame change on each ion)
    assert pulses(cx, ion, "rzz") == [("R", ("t",), (-PI / 2, PI / 2)), ("RZZ", ("c", "t"), (PI / 2,)),
                                      ("R", ("t",), (PI / 2, PI)), ("VZ", ("c",), (-PI / 2,)),
                                      ("VZ", ("t",), (-PI / 2,))]
    assert pulses(cz, ion, "rzz") == [("RZZ", ("c", "t"), (PI / 2,)), ("VZ", ("c",), (-PI / 2,)),
                                      ("VZ", ("t",), (-PI / 2,))]


def test_no_cx_is_one_rzz_and_a_single_one_qubit_pulse(U):
    """Two one-qubit pulses is the fewest: every CX from one RZZ(+-pi/2), ONE quarter- or half-turn
    pulse on either ion, before or after it, and any frame changes, is searched and none is a CX.
    (A frame change on the ion the pulse does not touch commutes with everything here, so the
    frames before the entangling pulse are searched on the pulsed ion only.)"""
    quarter = [k * PI / 2 for k in (-1, 0, 1, 2)]
    cx = U.gate_matrix("cx", [])[0]
    found = 0
    for q, theta, phi, sign in itertools.product((0, 1), (PI / 2, -PI / 2, PI), quarter, (PI / 2, -PI / 2)):
        r, zz = U.r_pulse(theta, phi), U.rzz(sign)
        for first in ("pulse", "zz"):
            for pre in quarter:
                M = U.apply_gate(np.eye(4, dtype=complex), U.vz(pre), [q], 2)
                for g, on in ((r, [q]), (zz, [0, 1])) if first == "pulse" else ((zz, [0, 1]), (r, [q])):
                    M = U.apply_gate(M, g, on, 2)
                for a, b in itertools.product(quarter, quarter):
                    N = U.apply_gate(U.apply_gate(M, U.vz(a), [0], 2), U.vz(b), [1], 2)
                    found += bool(U.same_up_to_phase(N, cx)[0])
    assert found == 0


def test_the_default_set_is_the_lowering_it_always_was():
    """Leaving the option out is `ms`, and `ms` is the pulse table from before there was a second."""
    ion = {0: "a", 1: "b"}
    assert gate_sets.DEFAULT == "ms" and gate_sets.GATE_SETS == SETS
    cx = [("R", ("a",), (PI / 2, PI / 2)), ("MS", ("a", "b"), (PI / 2,)), ("R", ("a",), (-PI / 2, 0.0)),
          ("R", ("b",), (-PI / 2, 0.0)), ("R", ("a",), (-PI / 2, PI / 2))]
    h = [("R", ("b",), (PI / 2, PI / 2 - PI)), ("VZ", ("b",), (PI,))]
    assert pulses(SourceOp(0, "cx", (0, 1), 1), ion) == pulses(SourceOp(0, "cx", (0, 1), 1), ion, "ms") == cx
    assert pulses(SourceOp(0, "cz", (0, 1), 1), ion) == h + cx + h
    for name in U3:
        op = SourceOp(0, name, (0,), 1)
        assert pulses(op, ion) == pulses(op, ion, "ms") == pulses(op, ion, "rzz")   # one-qubit gates: the same
    with pytest.raises(ValueError, match="the sets are ms, rzz"):
        pulses(SourceOp(0, "cx", (0, 1), 1), ion, "cz")
    with pytest.raises(ValueError, match="the sets are ms, rzz"):
        gate_sets.resolve("RXX")
    assert gate_sets.resolve(None) == gate_sets.resolve("MS") == "ms" and gate_sets.resolve(" rzz ") == "rzz"


# ------------------------------------------------------------------ the compilers

#: the physics a device may carry: its own (what its generator writes, priced at `qccdsim_jones`,
#: qccd-noise@1's clock), qccd-physics@2 (`quantinuum`, qccd-noise@2's) or qccd-physics@3
#: (`helios`, qccd-noise@3's: the Helios nominal model, where a frame change takes no time)
PHYS = {v: json.loads((ROOT / "qccd" / "workspace" / "physics" / f"qccd-physics@{v}.json").read_text(encoding="utf-8"))
        for v in ("2", "3")}
BLOCKS = ("primitives", "heating", "species", "budget")


def _device(router: str, physics: str = "3") -> dict:
    """The small machine each compiler is tried on, under `physics` ("own", "2" or "3")."""
    if router == "c2lr":
        doc = Machine.c2lr_grid(3, 3, species="Yb-Ba").arch.to_json()
        family = lambda prims: {**prims, **{k: dict(v) for k, v in c2lr.C2LR_DECK["Yb-Ba"].items()}}  # noqa: E731
    elif router == "wise":
        doc = wise.device_doc(3, 2, 2, name="wise_h3_2x2")
        family = wise.primitives
    elif router == "cyclone":
        doc = cyclone.device_doc(4, 3, name="cyclone4_n3")
        family = lambda prims: cyclone.primitives(prims, 3)  # noqa: E731
    elif router == "qccdc":
        doc = json.loads((ROOT / "arch" / "grid9x9.arch.json").read_text(encoding="utf-8"))
        family = dict
    elif router == "qccdc-rotate":
        doc = Machine.ring(8, 2, 8).arch.to_json()
        family = dict
    else:
        raise KeyError(router)
    if physics != "own":
        ph = PHYS[physics]
        species = dict(ph["species"])
        if router == "c2lr":
            species.update(pair="Yb-Ba", pair_source="arXiv:2403.00756 v2")
        doc.update(primitives=family(ph["primitives"]), heating=ph["heating"], species=species, budget=ph["budget"])
    return doc


#: each compiler's device physics, the table its cooling is priced at, and the rule sets its
#: programmes are judged by (`goals/goal3/families.RULES`; the C2LR boards' two; the standard
#: rules for qccdc)
SETUP = {"c2lr": ("own", "qccdsim_jones", ("c2lr", "c2lr-demonstrated")),
         "wise": ("3", TABLE, ("wise",)),
         "cyclone": ("3", TABLE, ("cyclone-deck",)),
         "qccdc": ("own", "qccdsim_jones", ()),
         "qccdc-rotate": ("own", "qccdsim_jones", ())}
#: the noise models, each on a machine whose physics prices its clock: qccd-noise@1 on the
#: machine as its generator writes it (the WISE and Cyclone generators write no physics of their
#: own, so theirs is the Helios nominal one they are compiled under), qccd-noise@2 on
#: qccd-physics@2, qccd-noise@3 on qccd-physics@3
NOISE = (("own", "qccd-noise@1"), ("2", "qccd-noise@2"), ("3", "qccd-noise@3"))


def _qccdc():
    """The compiler built from this source, when it has the option: (path, why not)."""
    exe = next((p for p in (ROOT / "Compiler" / "ocaml" / "_build" / "default" / "bin" / n
                            for n in ("qccdc_cli.exe", "qccdc_cli")) if p.is_file()), None)
    if exe is None:
        return None, "qccdc_cli is not built from source here (Compiler/ocaml: dune build)"
    if "rzz" not in gate_sets.binary_gate_sets(exe):
        return None, f"the qccdc_cli build at {exe} is older than the gate-set option: rebuild it"
    return exe, ""


class _Compiled:
    """One compile: the certified programme, the cooled one the machine plays, the certificate."""

    def __init__(self, raw: TSIR, prog: TSIR, cert: dict, arch: Architecture, qasm_path: Path, work: Path | None = None):
        self.raw, self.prog, self.cert, self.arch, self.qasm_path, self.work = raw, prog, cert, arch, qasm_path, work

    def bytes(self) -> tuple:
        return tuple(json.dumps(x, sort_keys=True) for x in (self.raw.to_json(), self.prog.to_json(), self.cert))


def _cooled(prog: TSIR, arch: Architecture, table: str, lazy: bool) -> TSIR:
    from qccd.compile.cooling import CoolingPolicy, insert_cooling
    out = insert_cooling(prog, arch, corrected_model(table), policy=CoolingPolicy(lazy=lazy)).program
    out.meta.update(prog.meta)
    return out


@pytest.fixture(scope="module")
def compiled(tmp_path_factory):
    """(compiler, circuit, gate set or None) -> the compile; None leaves the option out.

    The routers are called in process (`compile_c2lr`, `compile_wise`, `compile_cyclone`) and
    cooled as their `compile_with_router` cools them; qccdc is the binary built from this source,
    run on the expanded device with `--gate-set`, its stage evidence replayed by `check-stages`."""
    import subprocess
    cache: dict = {}
    base = tmp_path_factory.mktemp("gate_set")
    expanded: dict = {}

    def get(router: str, circuit: str, gate_set: str | None) -> _Compiled:
        key = (router, circuit, gate_set)
        if key in cache:
            return cache[key]
        qasm, n_data = CIRCUITS[circuit]
        physics, table, _ = SETUP[router]
        arch = Architecture.from_json(_device(router, physics))
        work = base / f"{router}_{circuit}_{gate_set or 'default'}"
        work.mkdir()
        qasm_path = work / "circuit.qasm"
        qasm_path.write_text(qasm, encoding="utf-8")
        kw = {} if gate_set is None else {"gate_set": gate_set}
        if router.startswith("qccdc"):
            exe, why = _qccdc()
            if exe is None:
                pytest.skip(why)
            if router not in expanded:
                expanded[router] = base / f"{router}.expanded.json"
                expanded[router].write_text(json.dumps(_load("export_arch").expand(arch)), encoding="utf-8")
            mode = "rotate" if router == "qccdc-rotate" else "compile"
            cmd = [str(exe), mode, str(qasm_path), "--arch", str(expanded[router]), "-o", str(work / "prog")]
            done = subprocess.run(cmd + (["--gate-set", gate_set] if gate_set else []), capture_output=True,
                                  text=True, timeout=300)
            assert done.returncode == 0, done.stdout[-600:] + done.stderr[-600:]
            stages = subprocess.run([str(exe), "check-stages", str(work / "prog.stages.json"), "--qasm",
                                     str(qasm_path), "--arch", str(expanded[router]), "--program",
                                     str(work / "prog.tsir.json")], capture_output=True, text=True, timeout=300)
            assert stages.returncode == 0 and '"accepted":true' in stages.stdout, stages.stdout + stages.stderr
            raw = TSIR.load(str(work / "prog.tsir.json"))
            cert = json.loads((work / "prog.qcert.json").read_text(encoding="utf-8"))
            lazy = False
        elif router == "c2lr":
            raw, cert = c2lr_router.compile_c2lr(qasm, arch, **kw)
            lazy = False
        elif router == "wise":
            raw, cert = wise_router.compile_wise(qasm, arch, **kw)
            lazy = True
        else:
            raw, cert = cyclone_router.compile_cyclone(qasm, arch, n_data, **kw)
            lazy = True
        cache[key] = _Compiled(raw, _cooled(raw, arch, table, lazy), cert, arch, qasm_path, work)
        return cache[key]

    return get


ROUTERS = ("c2lr", "wise", "cyclone", "qccdc")
CASES = [(r, c) for r in ROUTERS for c in CIRCUITS] + [("qccdc-rotate", "rep3")]


def _gates(prog: TSIR) -> dict:
    out: dict = {}
    for i in prog.instructions:
        if i.type == "gate":
            out[i.gate] = out.get(i.gate, 0) + (len(i.pairs) or len(i.ions))
    return out


def _rules_failed(router: str, c: _Compiled) -> dict:
    """What the compiler's own rule sets fail on the cooled programme: nothing, in either gate set."""
    _, table, sets = SETUP[router]
    if not sets:
        rep = verify(c.prog, c.arch, corrected_model(table), check_metrics=False)
        return {r: 1 for r in rep.rules.summary().get("failed") or []}
    failed: dict = {}
    for name in sets:
        rs = load_rules(name)
        exe = programme_execution(c.prog, c.arch, corrected_model(table), circuit=circuit_of_certificate(c.cert),
                                  rule_config=legacy_config(rs) or None, check_metrics=False)
        failed.update({f"{name}:{x.id}": x.count for x in check(rs, exe).results if x.verdict == FAILS})
    return failed


def _r10(cc, c: _Compiled):
    """Every check of check_cert.py that needs no Lean checker; returns the programme's tableau,
    composed from its emitted pulses with each readout deferred in place."""
    qc = cc.load_source(c.qasm_path)
    src = cc.source_ops(qc)
    assert cc.certificate_shape(c.cert) == []
    assert cc.check_binding(c.cert, qc, src) == []
    assert cc.check_o1(c.cert, c.arch) == []
    assert cc.check_witnesses(c.cert, c.raw) == []
    assert cc.check_program_transport(c.cert, c.raw, c.arch) == []
    assert cc.check_program_order(c.cert, c.raw) == []
    n = c.cert["n_qubits"]
    qubit_of = {ion: int(k) for k, ion in c.cert["map"].items()}
    anc, problems = cc.readout_plan(src, list(cc.program_steps(c.raw, qubit_of)), n)
    assert problems == []
    got = cc.tableau_from_program(c.raw, c.cert, n, anc)
    assert got == cc.circuit_tableau(c.qasm_path, n, src, anc)
    return got


def _two_qubit(circuit: str) -> tuple[int, int]:
    """(CX, CZ) statements of a circuit."""
    lines = CIRCUITS[circuit][0].splitlines()
    return sum(ln.startswith("cx ") for ln in lines), sum(ln.startswith("cz ") for ln in lines)


@pytest.mark.parametrize("router,circuit", CASES)
def test_both_sets_compile_keep_the_rules_and_implement_the_circuit(compiled, cc, router, circuit):
    n_cx, n_cz = _two_qubit(circuit)
    tableaux = {}
    for gs in SETS:
        c = compiled(router, circuit, gs)
        # the set's pulses and no others: one entangling pulse per two-qubit gate of the circuit
        for prog in (c.raw, c.prog):
            assert gate_sets.of_program(prog) == gs
            g = _gates(prog)
            assert set(g) <= set(gate_sets.NATIVE[gs]) | {"I"}, g       # I: WISE's shim charge, no pulse
            assert g[gate_sets.ENTANGLER[gs]] == n_cx + n_cz
        assert _rules_failed(router, c) == {}, (router, circuit, gs)
        tableaux[gs] = _r10(cc, c)
        # what each says of its gate set: the default nothing, the other its name
        if gs == "ms":
            assert "gate_set" not in c.prog.meta and "gate_set" not in c.raw.meta
            assert "gate_set" not in c.cert["claims"] and "rzz_gates" not in c.cert["claims"]
        else:
            assert c.prog.meta["gate_set"] == c.raw.meta["gate_set"] == "rzz"
            assert c.cert["claims"]["gate_set"] == "rzz"
            if "ms_gates" in c.cert["claims"]:
                assert c.cert["claims"]["ms_gates"] == 0 and c.cert["claims"]["rzz_gates"] == n_cx + n_cz
    # the two programmes are the same Clifford (each is the source's), measured in the same places
    assert tableaux["ms"] == tableaux["rzz"]
    ms, rzz = compiled(router, circuit, "ms"), compiled(router, circuit, "rzz")
    # ...and the rzz one fires fewer lasers: two one-qubit pulses fewer for every CX, six for every CZ
    assert _gates(rzz.raw).get("R", 0) < _gates(ms.raw).get("R", 0)
    if not router.startswith("qccdc"):   # qccdc's ms set writes a beam for a zero angle too; the routers never do
        assert _gates(ms.raw)["R"] - _gates(rzz.raw)["R"] == 2 * n_cx + 6 * n_cz
        assert _gates(rzz.raw)["VZ"] - _gates(ms.raw).get("VZ", 0) == 2 * n_cx


@pytest.mark.parametrize("router", ("c2lr", "wise", "cyclone"))
def test_the_transport_does_not_depend_on_the_gate_set(compiled, router):
    """A router decides where the ions go from the circuit, never from the pulses: the two sets'
    programmes start from the same placement and move the same ions the same way, in the same order."""
    def moves(prog: TSIR):
        return [(i.cls, json.dumps(i.template, sort_keys=True),
                 tuple((p.ion, p.src, p.dst, tuple(p.via)) for p in i.participants))
                for i in prog.instructions if i.type == "simd"]
    for circuit in CIRCUITS:
        ms, rzz = compiled(router, circuit, "ms"), compiled(router, circuit, "rzz")
        assert moves(ms.raw) == moves(rzz.raw), (router, circuit)
        assert ms.cert["init"] == rzz.cert["init"]
        strip = lambda ms_: [{k: v for k, v in m.items() if k != "t"} for m in ms_]  # noqa: E731
        assert strip(ms.cert["moves"]) == strip(rzz.cert["moves"])


@pytest.mark.parametrize("router", ROUTERS)
def test_leaving_the_option_out_is_the_default_set(compiled, router):
    """`gate_set="ms"` and no `gate_set` at all write the same programme, cooled programme and
    certificate, byte for byte; neither names a gate set or holds an RZZ."""
    for circuit in ("bell", "rep3"):
        named, left_out = compiled(router, circuit, "ms"), compiled(router, circuit, None)
        assert named.bytes() == left_out.bytes()
        assert not any("RZZ" in x or "gate_set" in x for x in named.bytes())
        assert named.bytes() != compiled(router, circuit, "rzz").bytes()


def test_the_file_entry_points_take_the_option(tmp_path, compiled):
    """`compile_with_router(..., gate_set=)`: the files a caller is handed (the certified programme,
    the cooled one, the certificate, compile.json) are the in-process compile's, and say the set."""
    for router in ("c2lr", "cyclone"):
        physics, table, _ = SETUP[router]
        arch_path = tmp_path / f"{router}.arch.json"
        arch_path.write_text(json.dumps(_device(router, physics)), encoding="utf-8")
        work = tmp_path / router
        if router == "c2lr":
            final, cert = c2lr_router.compile_with_router(BELL, arch_path, work, gate_set="rzz")
        elif router == "wise":
            final, cert = wise_router.compile_with_router(BELL, arch_path, work, table=table, gate_set="rzz")
        else:
            final, cert = cyclone_router.compile_with_router(BELL, arch_path, work, n_data=1, table=table,
                                                             gate_set="rzz")
        want = compiled(router, "bell", "rzz")
        assert TSIR.load(str(work / "prog.tsir.json")).to_json() == want.raw.to_json()
        assert TSIR.load(str(final)).to_json() == want.prog.to_json()
        assert json.loads(Path(cert).read_text(encoding="utf-8")) == want.cert
        assert TSIR.load(str(final)).meta["gate_set"] == "rzz"
    assert json.loads((tmp_path / "c2lr" / "compile.json").read_text(encoding="utf-8"))["gate_set"] == "rzz"


def test_compile_with_qccdc_takes_the_option(tmp_path, monkeypatch, compiled):
    """The reference compile's entry point: `--gate-set` reaches qccdc, the cooled programme keeps
    the set's name, and compile.json records it."""
    exe, why = _qccdc()
    if exe is None:
        pytest.skip(why)
    monkeypatch.setenv("QCCD_QCCDC", str(exe))               # the build of THIS source, not an installed one
    final, cert = compile_with_qccdc(BELL, ROOT / "arch" / "grid9x9.arch.json", tmp_path, mode="compile",
                                     timeout=300, gate_set="rzz")
    prog = TSIR.load(str(final))
    assert gate_sets.of_program(prog) == "rzz" and prog.meta["gate_set"] == "rzz"
    assert json.loads((tmp_path / "compile.json").read_text(encoding="utf-8"))["gate_set"] == "rzz"
    want = compiled("qccdc", "bell", "rzz")
    assert TSIR.load(str(tmp_path / "prog.tsir.json")).to_json() == want.raw.to_json()
    assert json.loads(Path(cert).read_text(encoding="utf-8"))["gates"] == want.cert["gates"]


# ------------------------------------------------------------------ the noisy circuit


@pytest.mark.parametrize("router", ROUTERS + ("qccdc-rotate",))
def test_the_memory_extracts_and_samples_in_both_sets(compiled, router):
    """The compiled memory under each noise model, on the machine with that model's physics: a
    stim circuit whose detectors are deterministic with the source's parity, the same detectors
    and the same noiseless circuit shape in the two sets, noise on every entangling pulse, and
    samples a decoder can be handed."""
    shape: dict = {}
    for gs in SETS:
        c = compiled(router, "rep3", gs)
        n_two = _gates(c.prog)[gate_sets.ENTANGLER[gs]]
        for physics, noise in NOISE:
            if physics == "own" and router in ("wise", "cyclone"):
                physics = "3"
            arch = Architecture.from_json(_device(router, physics))
            ex = extract(c.prog, arch, MEMORY, noise=noise, cert=c.cert)
            chk = noiseless_check(ex.circuit)
            assert chk.ok, (router, gs, noise, chk.to_json())
            assert ex.stats["n_ms"] == n_two and ex.stats["cert_crosscheck"] == "agrees"
            text = str(ex.circuit)
            # the entangling pulse as its own Clifford, and never the other set's
            assert ("SQRT_ZZ" in text) == (gs == "rzz") and ("SQRT_XX" in text) == (gs == "ms")
            # every entangling pulse carries the two-qubit channel (a Cyclone gate swap carries one too)
            assert text.count("DEPOLARIZE2") >= n_two and ex.budget["ms_base"] > 0
            clean = ex.circuit.without_noise()
            det, obs = clean.compile_detector_sampler(seed=1).sample(64, separate_observables=True)
            assert not det.any() and not obs.any()
            shape.setdefault(noise, {})[gs] = (clean.num_measurements, clean.num_detectors, clean.num_observables,
                                               text.count("DEPOLARIZE2"))
            dem = ex.circuit.detector_error_model(decompose_errors=True)
            assert dem.num_detectors == clean.num_detectors and dem.num_errors > 0
            dets, flips = ex.circuit.compile_detector_sampler(seed=7).sample(500, separate_observables=True)
            assert dets.shape == (500, clean.num_detectors) and flips.shape == (500, clean.num_observables)
    for noise, by in shape.items():
        assert by["ms"] == by["rzz"], (router, noise)


def test_an_rzz_pulse_is_priced_and_noised_as_the_two_qubit_primitive(compiled):
    """The same circuit on the same machine: every entangling pulse takes `ms_gate`'s time in
    either set, and the two-qubit channel books the same base error for each."""
    from qccd.verify.replay import replay
    ms, rzz = compiled("wise", "rep3", "ms"), compiled("wise", "rep3", "rzz")
    model = corrected_model(TABLE)
    spans = {}
    for name, c in (("MS", ms), ("RZZ", rzz)):
        res = replay(c.prog, c.arch, model, check_rules=False, keep_cycles=True)
        by_id = {ins.id: ins for ins in c.prog.instructions}
        us = {round(cyc.t1 - cyc.t0, 6) for cyc in res.cycles if by_id[cyc.instr_id].gate == name}
        assert len(us) == 1
        spans[name] = us.pop()
    assert spans["MS"] == spans["RZZ"] > 0
    base = {gs: extract(c.prog, c.arch, MEMORY, noise="qccd-noise@3", cert=c.cert).budget["ms_base"]
            for gs, c in (("ms", ms), ("rzz", rzz))}
    assert base["ms"] == pytest.approx(base["rzz"])
    # the hand-written extraction the published entries were graded with reads the pulse as the
    # noise text does: the same circuit, operation for operation, in either set
    from qccd.evalset.compare import same_circuit
    from qccd.qec.legacy_extract import extract_legacy
    arch = Architecture.from_json(_device("wise", "2"))
    for c in (ms, rzz):
        new = extract(c.prog, arch, MEMORY, noise="qccd-noise@2", cert=c.cert)
        old = extract_legacy(c.prog, arch, MEMORY, noise="qccd-noise@2", cert=c.cert)
        ok, why = same_circuit(old.circuit, new.circuit)
        assert ok, why


def test_w1_reads_either_entangling_pulse_as_the_pair_s_gate(compiled):
    """WISE's W1 (a zone holds two ions only on the way to a gate or a split) names the two-ion
    gate by what it is: the rzz programme passes it, and the same step under a name that is no
    entangling pulse fails it."""
    c = compiled("wise", "rep3", "rzz")
    rs = load_rules("wise")

    def failed(prog):
        exe = programme_execution(prog, c.arch, corrected_model(TABLE), circuit=circuit_of_certificate(c.cert),
                                  rule_config=legacy_config(rs) or None, check_metrics=False)
        return {x.id for x in check(rs, exe).results if x.verdict == FAILS}

    assert failed(c.prog) == set()
    doc = c.prog.to_json()
    for ins in doc["instructions"]:
        if ins.get("gate") == "RZZ":
            ins["gate"] = "ZZX"
    assert "W1" in failed(TSIR.from_json(doc))


# ------------------------------------------------------------------ the other routers


def test_every_router_takes_the_option():
    """Each public compile entry point has `gate_set`, defaulting to the default set."""
    import inspect

    from qccd.compile import tiled_cat, tiled_surface
    entries = [c2lr_router.compile_c2lr, c2lr_router.compile_with_router, c2lr_router.compile_with_stages,
               wise_router.compile_wise, wise_router.compile_with_router,
               cyclone_router.compile_cyclone, cyclone_router.compile_with_router,
               helios_router.compile_helios, helios_router.compile_with_router,
               conveyor_router.compile_conveyor, conveyor_router.compile_with_router,
               tiled_surface.compile_tiled, tiled_cat.play_rep, tiled_cat.play_wide, compile_with_qccdc]
    for fn in entries:
        p = inspect.signature(fn).parameters.get("gate_set")
        assert p is not None and p.default == "ms", fn.__qualname__


def test_the_helios_router_in_its_own_gate_set(cc, tmp_path):
    """Helios runs R, VZ and RZZ (arXiv:2511.05465): the batch router lowers to them, and the
    programme passes the Helios rules and is the circuit."""
    from qccd.arch import helios
    doc = helios.device_doc("helios_98")
    doc.update({b: PHYS["3"][b] for b in BLOCKS})
    arch = Architecture.from_json(doc)
    raw, cert = helios_router.compile_helios(GHZ, arch, gate_set="rzz")
    (tmp_path / "circuit.qasm").write_text(GHZ, encoding="utf-8")
    c = _Compiled(raw, _cooled(raw, arch, TABLE, True), cert, arch, tmp_path / "circuit.qasm")
    assert _gates(c.raw) == {"R": 5, "VZ": 7, "RZZ": 2} and c.prog.meta["gate_set"] == "rzz"
    rs = load_rules("helios-deck")
    exe = programme_execution(c.prog, c.arch, corrected_model(TABLE), circuit=circuit_of_certificate(c.cert),
                              rule_config=legacy_config(rs) or None, check_metrics=False)
    assert [x.id for x in check(rs, exe).results if x.verdict == FAILS] == []
    _r10(cc, c)


def test_the_conveyor_router_takes_the_option(cc, tmp_path):
    """The conveyor sandwich's router lowers through the same emitter: an X check and a Z check
    in rzz keep the standard rules, are the circuit, and move as the ms programme moves."""
    from qccd.arch.sandwich import conveyor_sandwich
    two_checks = ('OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[6];\ncreg c[2];\nh q[4];\ncx q[4],q[0];\n'
                  'cx q[0],q[5];\ncx q[4],q[1];\ncx q[1],q[5];\ncx q[4],q[2];\ncx q[3],q[5];\nh q[4];\n'
                  'measure q[4] -> c[0];\nmeasure q[5] -> c[1];\n')
    (tmp_path / "circuit.qasm").write_text(two_checks, encoding="utf-8")
    arch = conveyor_sandwich(4, layers=2).arch
    rs = load_rules("standard")
    moves = {}
    for gs in SETS:
        raw, cert = conveyor_router.compile_conveyor(two_checks, arch, 4, name="two_checks", gate_set=gs)
        c = _Compiled(raw, _cooled(raw, arch, "qccdsim_jones", False), cert, arch, tmp_path / "circuit.qasm")
        assert gate_sets.of_program(c.prog) == gs and _gates(c.raw)[gate_sets.ENTANGLER[gs]] == 6
        exe = programme_execution(c.prog, arch, corrected_model("qccdsim_jones"), circuit=circuit_of_certificate(cert),
                                  rule_config=legacy_config(rs) or None, check_metrics=False)
        assert [x.id for x in check(rs, exe).results if x.verdict == FAILS] == []
        _r10(cc, c)
        moves[gs] = [(i.cls, tuple((p.ion, p.src, p.dst) for p in i.participants))
                     for i in c.raw.instructions if i.type == "simd"]
    assert moves["ms"] == moves["rzz"] and moves["ms"]


def test_the_tiled_surface_compiler_takes_the_option(cc, tmp_path):
    """The tiled compiler shares the lowering: a d = 3 surface round in rzz is one RZZ a CNOT."""
    from qccd.compile.tiled_surface import compile_tiled
    from qccd.qec import rotated_surface
    exp = rotated_surface(3, rounds=1)
    out = {}
    for gs in SETS:
        prog, cert, arch = compile_tiled(exp.qasm, 3, gate_set=gs)
        out[gs] = (prog, cert)
        assert gate_sets.of_program(prog) == gs
        (tmp_path / f"{gs}.qasm").write_text(exp.qasm, encoding="utf-8")
        _r10(cc, _Compiled(prog, prog, cert, arch, tmp_path / f"{gs}.qasm"))
    n_cx = exp.qasm.count("cx ")
    assert _gates(out["ms"][0]).get("MS") == _gates(out["rzz"][0]).get("RZZ") == n_cx
    assert "gate_set" not in out["ms"][0].meta and out["rzz"][0].meta["gate_set"] == "rzz"
    assert out["ms"][1]["claims"].keys() == {"cycles", "beams", "ms_gates", "frames"}


# ------------------------------------------------------------------ text, replay, the bench's check


def _rzz(**kw) -> Instruction:
    return Instruction(type="gate", id=3, gate="RZZ", mode="intra", params=((PI / 2,),), pairs=(("q0", "q1"),),
                       sites=("G",), **kw)


def test_an_rzz_pulse_prints_and_reads_back():
    from qccd.ir import tsir_text
    from qccd.ir.target_text import call_text, instruction_text, parse_call
    one = _rzz(meta={"kind": "rzz", "op": [4]})
    assert instruction_text(one) == "pulse RZZ(pi/2) q0,q1 @G;"
    two = Instruction(type="gate", id=4, gate="RZZ", mode="intra", params=((PI / 2,), (-PI / 2,)),
                      pairs=(("q0", "q1"), ("q2", "q3")), sites=("G", "H"))
    assert instruction_text(two) == "broadcast RZZ(pi/2) q0,q1@G, RZZ(-pi/2) q2,q3@H;"
    # as the MS pulse prints, name for name
    assert instruction_text(_rzz()).replace("RZZ", "MS") == \
        instruction_text(Instruction(type="gate", id=3, gate="MS", mode="intra", params=((PI / 2,),),
                                     pairs=(("q0", "q1"),), sites=("G",)))
    # the TSIR text reads back to the same instruction; the JSON too
    for ins in (one, two):
        assert tsir_text.parse_instruction(tsir_text.instruction_text(ins)) == ins
        assert Instruction.from_json(json.loads(json.dumps(ins.to_json()))) == ins
    # and an authored statement naming the gate is a call that reads back
    call = ("gate", ["RZZ", [["d0", "a0"]], ["S0"]], {})
    assert call_text(*call) == "gate RZZ d0,a0 @S0;" and parse_call(call_text(*call)) == call


def test_the_checkers_read_an_rzz_pulse_and_refuse_what_is_not_native(cc, U):
    """`check_cert` composes RZZ by its own Clifford and its own unitary; a pulse of no gate set
    is refused by name, never skipped."""
    cert = {"map": {"0": "q0", "1": "q1"}}
    prog = TSIR(name="t", arch_spec="", instructions=[_rzz()])
    assert list(cc.program_steps(prog, {"q0": 0, "q1": 1})) == [("RZZ", (0, 1), (PI / 2,))]
    want = stim.Tableau.from_circuit(stim.Circuit("SQRT_ZZ 0 1"))
    assert cc.tableau_from_program(prog, cert, 2) == want
    assert U.same_up_to_phase(cc.program_unitary(prog, cert, 2), U.rzz(PI / 2))[0]
    # the same angle as an MS is another Clifford: the name is read, not assumed
    ms = TSIR(name="t", arch_spec="", instructions=[Instruction(type="gate", id=3, gate="MS", mode="intra",
                                                                params=((PI / 2,),), pairs=(("q0", "q1"),))])
    assert cc.tableau_from_program(ms, cert, 2) != want
    # a non-Clifford angle is outside the tableau and inside the unitary
    t = TSIR(name="t", arch_spec="", instructions=[_rzz().__class__(type="gate", id=3, gate="RZZ", mode="intra",
                                                                    params=((0.3,),), pairs=(("q0", "q1"),))])
    with pytest.raises(NotImplementedError, match="non-Clifford RZZ"):
        cc.tableau_from_program(t, cert, 2)
    assert U.same_up_to_phase(cc.program_unitary(t, cert, 2), U.gate_matrix("rzz", [0.3])[0])[0]
    bad = TSIR(name="t", arch_spec="", instructions=[Instruction(type="gate", id=3, gate="RXY", mode="intra",
                                                                 params=((PI / 2,),), pairs=(("q0", "q1"),))])
    with pytest.raises(NotImplementedError, match="not a native pulse"):
        list(cc.program_steps(bad, {"q0": 0, "q1": 1}))
    with pytest.raises(NotImplementedError, match="not a native pulse"):
        U.program_unitary(bad.instructions, {"q0": 0, "q1": 1}, 2)


def test_the_bench_takes_a_programme_of_one_gate_set_the_suite_names(tmp_path):
    """The Compiler board's `native` check: a suite's programmes are in the default set unless its
    manifest names others, and a programme is in ONE set."""
    from types import SimpleNamespace

    from qccd.bench import check as bench
    assert bench.NATIVE_GATES == {"R", "VZ", "MS"}
    assert {k: set(v) for k, v in bench.NATIVE_BY_SET.items()} == {"ms": {"R", "VZ", "MS"}, "rzz": {"R", "VZ", "RZZ"}}
    pair = SimpleNamespace(n_qubits=2)

    def grade(gates, **kw):
        ins = [{"type": "init", "id": 0, "placement": {"q0": "A", "q1": "A"}, "quanta": {"q0": 0.0, "q1": 0.0}}]
        for k, g in enumerate(gates, 1):
            ins.append({"type": "gate", "id": k, "gate": g, "mode": "intra", "params": [[PI / 2]],
                        "pairs": [["q0", "q1"]]})
        p = tmp_path / "p.tsir.json"
        p.write_text(json.dumps({"tsir_version": "0.2", "name": "p", "arch_spec": "arch/x.arch.json",
                                 "instructions": ins}), encoding="utf-8")
        return bench.grade_pair(pair, p, physics={}, ler=None, **kw)

    # past `native`, the next check (the qubit map this stub does not carry) is what stops it
    ok = grade(["MS"])
    assert ok["checks"]["native"] == {"ok": True} and ok["reason"].startswith("qubit_map")
    refused = grade(["RZZ"])
    assert refused["status"] == "wrong" and refused["reason"] == "native: gates ['RZZ'] are not native (R, VZ, MS)"
    both = ("ms", "rzz")
    ok = grade(["RZZ"], gate_sets=both)
    assert ok["checks"]["native"] == {"ok": True, "gate_set": "rzz"} and ok["reason"].startswith("qubit_map")
    assert grade(["MS"], gate_sets=both)["checks"]["native"] == {"ok": True}
    mixed = grade(["MS", "RZZ"], gate_sets=both)
    assert mixed["status"] == "wrong" and "more than one gate set" in mixed["reason"]
    assert grade(["CX"], gate_sets=both)["reason"] == \
        "native: gates ['CX'] are not native (R, VZ, MS; or R, VZ, RZZ)"


# ------------------------------------------------------------------ a compiler from before the option


def test_a_binary_from_before_the_option_is_refused_not_run(tmp_path, monkeypatch):
    """A qccdc built before `--gate-set` ignores the flag and compiles MS.  It is asked what it
    lowers to first; one that cannot say is refused with the way out, and nothing is compiled."""
    # any executable with no `gate-sets` command answers as an old qccdc does: the default set alone
    old = Path(sys.executable)
    assert gate_sets.binary_gate_sets(old) == ("ms",)
    assert gate_sets.binary_refusal(old, "ms") is None
    why = gate_sets.binary_refusal(old, "rzz")
    assert why and "lowers to the gate set ms only" in why and "dune build" in why and "QCCD_QCCDC" in why
    monkeypatch.setenv("QCCD_QCCDC", str(old))
    with pytest.raises(CompileFailed) as e:
        compile_with_qccdc(BELL, ROOT / "arch" / "grid9x9.arch.json", tmp_path / "w", gate_set="rzz")
    assert e.value.kind == "gate_set_unsupported" and "toolchain release" in str(e.value)
    assert not list((tmp_path / "w").glob("prog*"))          # nothing was compiled, in any set
    with pytest.raises(ValueError, match="the sets are ms, rzz"):
        compile_with_qccdc(BELL, ROOT / "arch" / "grid9x9.arch.json", tmp_path / "w", gate_set="xx")


def test_the_released_compiler_is_refused_for_rzz():
    """The pinned release (qccd/workspace/toolchain.json) is from before the option."""
    from qccd.workspace.toolchain import installed
    exe = installed()
    if exe is None:
        pytest.skip("no released qccdc_cli is installed here (qccd toolchain install)")
    if "rzz" in gate_sets.binary_gate_sets(exe):
        pytest.skip("the installed release already has the gate-set option")
    assert "lowers to the gate set ms only" in gate_sets.binary_refusal(exe, "rzz")


def test_the_built_compiler_names_its_gate_sets_and_checks_its_own_table():
    import subprocess
    exe, why = _qccdc()
    if exe is None:
        pytest.skip(why)
    assert gate_sets.binary_gate_sets(exe) == SETS
    for gs, entangler, cx, cz in (("ms", "MS", (4, 1), (6, 1)), ("rzz", "RZZ", (2, 1), (0, 1))):
        done = subprocess.run([str(exe), "pulses-selftest", "--gate-set", gs], capture_output=True, text=True,
                              timeout=120)
        assert done.returncode == 0 and "PULSES PASS" in done.stdout, done.stdout[-400:]
        rows = {ln.split()[0]: ln.split() for ln in done.stdout.splitlines() if ln and ln[0].isalpha()}
        assert rows["gate"][-1] == entangler
        assert (int(rows["cx"][-2]), int(rows["cx"][-1])) == cx
        assert (int(rows["cz"][-2]), int(rows["cz"][-1])) == cz
    bad = subprocess.run([str(exe), "pulses-selftest", "--gate-set", "xx"], capture_output=True, text=True, timeout=60)
    assert bad.returncode == 2 and "the gate sets are ms, rzz" in bad.stderr


# ------------------------------------------------------------------ the workspace's switch


def test_a_workspace_job_takes_the_gate_set_and_refuses_what_cannot_compile_it(tmp_path, monkeypatch):
    from qccd.workspace.app import Workspace, WorkspaceError
    human = {"kind": "human", "id": "cli"}
    ws = Workspace.init(tmp_path / "ws", "ghz4@1")
    try:
        with pytest.raises(WorkspaceError) as e:
            ws.start_job(human, "run", {"program": "ghz4", "gate_set": "xx"})
        assert e.value.status == 422 and "the sets are ms, rzz" in str(e.value)
        with pytest.raises(WorkspaceError) as e:
            ws.start_job(human, "evaluate", {"gate_set": "rzz"})
        assert e.value.status == 422 and "takes no gate_set" in str(e.value)
        # QCCDCert lowers to MS only: asking for it AND another set is refused when the job is started
        with pytest.raises(WorkspaceError) as e:
            ws.start_job(human, "run", {"program": "ghz4", "gate_set": "rzz", "compiler": "qccdcert"})
        assert e.value.status == 422 and "QCCDCert" in str(e.value) and "ms only" in str(e.value)
        # a compiler from before the option: refused at once, with the way out, for every kind that compiles
        monkeypatch.setenv("QCCD_QCCDC", sys.executable)
        for kind, params in (("run", {"program": "ghz4"}), ("compile", {}), ("ler", {"experiment": "rep3"})):
            with pytest.raises(WorkspaceError) as e:
                ws.start_job(human, kind, {**params, "gate_set": "rzz"})
            assert e.value.status == 424 and e.value.code == "toolchain_missing", kind
            assert "lowers to the gate set ms only" in str(e.value) and "dune build" in str(e.value)
    finally:
        ws.close()
