"""C2LR swap-or-stay transport, phase 3: the baseline router, the noise it is graded by, and the
boards (docs/c2lr.md section 8).

Held here:

* the router (`qccd.compile.c2lr_router`) compiles the memory boards' circuits onto C2LR lattices
  into programmes that pass every rule, both C2LR rule sets, and R10's two halves -- the
  certificate's binding, transport, witnesses and per-qubit order, the stabilizer tableau
  against the source, and (where the Lean checker is built) the proved checker;
* it never reorders: every op runs in its layer or a later one than the op before it, and
  commuting CNOTs on one qubit keep their order;
* it refuses, saying why, a device that is not a C2LR lattice, too few legs, a gate it has no
  pulses for;
* the noise reads the heat a C2LR step leaves AND the merge a gate in one leg begins with, as
  R7 does -- and the text and the hand-written extraction write the same circuit;
* the grader judges a release that declares a rule set by that set too; the bench grades a
  compiler on a board's circuit.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from qccd.api import Machine
from qccd.compile.c2lr_router import (Refused, compile_c2lr, compile_with_router, layers, parse_qasm)
from qccd.cost.models import corrected_model
from qccd.verify import verify
from qccd.workspace.tasks import find_release

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "Compiler" / "bridge"
REP5 = find_release("rep5_mem@1")
SURFACE3 = find_release("surface3_mem@1")


def _cc():
    spec = importlib.util.spec_from_file_location("check_cert_router", BRIDGE / "check_cert.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(BRIDGE))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(BRIDGE))
    return mod


def _r10(prog, cert, arch, qasm: str, tmp_path: Path) -> None:
    """Every check of check_cert.py that does not need the Lean checker."""
    cc = _cc()
    p = tmp_path / "c.qasm"
    p.write_text(qasm, encoding="utf-8")
    qc = cc.load_source(p)
    src = cc.source_ops(qc)
    assert cc.check_binding(cert, qc, src) == []
    assert cc.check_o1(cert, arch) == []
    assert cc.check_witnesses(cert, prog) == []
    assert cc.check_program_transport(cert, prog, arch) == []
    assert cc.check_program_order(cert, prog) == []
    qubit_of = {ion: int(q) for q, ion in cert["map"].items()}
    anc, problems = cc.readout_plan(src, list(cc.program_steps(prog, qubit_of)), cert["n_qubits"])
    assert problems == []
    assert cc.tableau_from_program(prog, cert, cert["n_qubits"], anc) == \
        cc.circuit_tableau(p, cert["n_qubits"], src, anc)


@pytest.mark.parametrize("release, size, species", [
    (REP5, (5, 2), "Ba-Sr"), (REP5, (3, 3), "Yb-Ba"), (SURFACE3, (4, 5), "Ba-Sr")])
def test_the_router_compiles_a_memory_board_and_every_check_holds(release, size, species, tmp_path):
    m = Machine.c2lr_grid(*size, species=species)
    arch_p = tmp_path / "dev.arch.json"
    arch_p.write_text(json.dumps(m.arch.to_json()), encoding="utf-8")
    final, cert_p = compile_with_router(release.circuit_text(), arch_p, tmp_path / "w")
    from qccd.ir.tsir import TSIR
    prog, certified = TSIR.load(final), TSIR.load(tmp_path / "w" / "prog.tsir.json")
    cert = json.loads(cert_p.read_text(encoding="utf-8"))
    for forms in (None, ("diagonal",)):        # c2lr, and c2lr-demonstrated's C6
        rep = verify(prog, m.arch, corrected_model(), check_metrics=False,
                     rule_config={"c2lr_forms": forms} if forms else None)
        assert rep.rules.summary().get("failed") == [], rep.rules.violations[:3]
        assert "assumed" not in rep.rules.summary()
    from qccd.ruleset import check, load
    from qccd.ruleset.programme import programme_execution
    for rs in ("c2lr", "c2lr-demonstrated"):
        lang = check(load(rs), programme_execution(prog, m.arch, corrected_model(), check_metrics=False))
        assert lang.ok, lang.failed()
    # only C2LR steps move anything, and only by the single diagonals
    steps = [i for i in prog.instructions if i.type == "simd"]
    assert steps and all(i.template and i.template.get("diagonal") for i in steps)
    _r10(certified, cert, m.arch, release.circuit_text(), tmp_path)


def test_the_proved_checker_accepts_the_routers_certificate(tmp_path):
    exe = os.environ.get("QCCD_QCHECK")
    if not exe or not Path(exe).is_file():
        from test_c2lr import _qcheck
        exe = _qcheck()
    if exe is None:
        pytest.skip("the Lean checker (qcheck) is not built here")
    m = Machine.c2lr_grid(5, 2)
    arch_p = tmp_path / "dev.arch.json"
    arch_p.write_text(json.dumps(m.arch.to_json()), encoding="utf-8")
    compile_with_router(REP5.circuit_text(), arch_p, tmp_path / "w")
    w = tmp_path / "w"
    subprocess.run([sys.executable, str(BRIDGE / "mk_qcheck_input.py"), str(w / "prog"), "--arch",
                    str(w / "device.expanded.json"), "-o", str(w / "q.json")], check=True, capture_output=True)
    out = subprocess.run([str(exe), str(w / "q.json")], capture_output=True, text=True, timeout=600)
    assert "ACCEPTED" in out.stdout, out.stdout + out.stderr


# ------------------------------------------------------------------------- order

def test_the_layers_never_run_an_op_before_one_that_precedes_it():
    for release in (REP5, SURFACE3):
        _, ops = parse_qasm(release.circuit_text())
        at = {op.i: k for k, layer in enumerate(layers(ops)) for op in layer}
        seq = [at[op.i] for op in ops if op.name != "barrier"]
        assert seq == sorted(seq)
        for layer in layers(ops):
            qs = [q for op in layer for q in op.qubits]
            assert len(qs) == len(set(qs))          # a layer shares no qubit


def test_commuting_cnots_on_one_qubit_keep_their_order(tmp_path):
    """Two CNOTs onto one target commute; the second may not run first, nor beside the first."""
    qasm = ('OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[3];\ncreg c[3];\n'
            'cx q[0],q[2];\ncx q[1],q[2];\nh q[0];\nmeasure q[2] -> c[0];\n')
    m = Machine.c2lr_grid(3, 3)
    prog, cert = compile_c2lr(qasm, m.arch)
    t = {g["dag"]: g["t"] for g in cert["gates"]}
    assert t[0] < t[1] and t[0] < t[2]
    assert _cc().check_program_order(cert, prog) == []
    _r10(prog, cert, m.arch, qasm, tmp_path)


# ------------------------------------------------------------------------- refusals

@pytest.mark.parametrize("arch, qasm, why", [
    (Machine.grid(3, 3).arch, REP5.circuit_text(), "does not declare C2LR control"),
    (Machine.c2lr_grid(2, 2).arch, REP5.circuit_text(), "9 qubits and 4 legs"),
    (Machine.c2lr_grid(3, 3).arch, 'OPENQASM 2.0;\nqreg q[2];\nccx q[0],q[1],q[1];\n', "not in the router's set"),
    (Machine.c2lr_grid(3, 3).arch, 'OPENQASM 2.0;\nqreg q[2];\nrz(0.3) q[0];\n', "parameterised gate"),
])
def test_what_the_router_cannot_compile_it_refuses_saying_why(arch, qasm, why):
    with pytest.raises(Refused, match=why):
        compile_c2lr(qasm, arch)


def test_the_contract_cli_exits_4_on_a_device_it_does_not_compile(tmp_path):
    from qccd.compile.c2lr_router import main
    dev = tmp_path / "grid.arch.json"
    dev.write_text(json.dumps(Machine.grid(3, 3).arch.to_json()), encoding="utf-8")
    c = tmp_path / "c.qasm"
    c.write_text(REP5.circuit_text(), encoding="utf-8")
    assert main(["--circuit", str(c), "--device", str(dev), "--expanded", str(dev),
                 "--out", str(tmp_path / "out")]) == 4


# ------------------------------------------------------------------------- the noise

def test_the_noise_reads_the_c2lr_heat_and_the_merge_as_r7_does():
    """A gate in one leg is merge, MS, split; the replay deposits the merge before R7 and R16
    read n-bar, and the noise text's `nbar(i, start(g))` must see the same number.  The step
    heat on the `c2lr` channel is in it too: a C2LR gate is not heat-free."""
    from test_c2lr import BA_SR, TOUR, build
    from qccd.evalset.model import build_run
    prog = build(TOUR)
    seen = {}
    rep = verify(prog, BA_SR.arch, corrected_model(), on_cycle=lambda v: v.instr.type == "gate"
                 and seen.setdefault(v.instr.id, dict(v.quanta)))
    run = build_run(prog, BA_SR.arch, "qccdsim_jones")
    gate = next(st for st in run.steps if st.kind == "gate")
    want = seen[gate.instr.id]
    assert gate.nbar0.get("q0") == pytest.approx(want["q0"]) == pytest.approx(rep.result.max_gate_quanta_seen)
    c2lr = rep.result.per_ion_quanta["q0"]["c2lr"]
    assert c2lr > 0 and gate.nbar0.get("q0") > 0.1 + 2 * 0.14 - 1e-9     # two moves, the merge


@pytest.mark.parametrize("noise", ["qccd-noise@1", "qccd-noise@2"])
def test_the_text_writes_the_hand_written_circuit_on_a_c2lr_memory(noise):
    pytest.importorskip("stim")
    from qccd.evalset import load
    from qccd.evalset.compare import same_circuit
    from qccd.qec import MemoryExperiment
    from qccd.qec.legacy_extract import extract_legacy
    m = Machine.c2lr_grid(5, 2)
    prog, cert = compile_c2lr(REP5.circuit_text(), m.arch)
    from qccd.compile.cooling import insert_cooling
    prog = insert_cooling(prog, m.arch, corrected_model()).program
    arch = m.arch
    if noise == "qccd-noise@2":
        # @2 prices at the "quantinuum" table: the lattice on qccd-physics@2's blocks
        from qccd.arch import Architecture
        from qccd.arch.c2lr import c2lr_primitives
        doc = m.arch.to_json()
        phys = json.loads((ROOT / "qccd/workspace/physics/qccd-physics@2.json").read_text(encoding="utf-8"))
        doc.update({b: phys[b] for b in ("primitives", "heating", "budget")})
        doc["primitives"] = {**doc["primitives"], **{k: v for k, v in c2lr_primitives("Ba-Sr").items()
                                                      if k not in ("merge", "split")}}
        arch = Architecture.from_json(doc)
    exp = MemoryExperiment.from_spec(REP5.detectors(), REP5.circuit_text())
    ref = extract_legacy(prog, arch, exp, noise=noise, cert=cert)
    ev = load(noise)
    mine = ev.circuit(ev.run(prog, arch, experiment=exp, cert=cert))
    ok, why = same_circuit(ref.circuit, mine.stim())
    assert ok, why


def test_the_metrics_count_a_swap_or_stay_step_as_transport():
    from qccd.workspace.metrics import broadcast_use, compute_metrics
    m = Machine.c2lr_grid(5, 2)
    prog, _ = compile_c2lr(REP5.circuit_text(), m.arch)
    values, _ = compute_metrics(m.arch, prog, REP5.manifest["physics"])
    steps = sum(1 for i in prog.instructions if i.type == "simd")
    assert values["transport_instructions"] == steps
    assert values["ions_per_instruction"] == pytest.approx(broadcast_use(m.arch, prog)["ions_per_instruction"])
    assert values["dacs"] == 32 + 2 * 13         # the census as broadcast wiring, under 128


# ------------------------------------------------------------------------- the grader and the bench

def test_the_grader_judges_a_release_by_the_rule_set_it_declares(tmp_path):
    from qccd.ruleset import legacy_config, load
    from qccd.workspace.evaluator import ALLOWED_PARTIAL, _Stage, _rules_stage
    from qccd.workspace.tasks import TaskRelease
    assert legacy_config(load("c2lr-demonstrated")) == {"c2lr_forms": ("diagonal",)}
    assert "C8" in ALLOWED_PARTIAL
    m = Machine.c2lr_grid(3, 3)
    from test_c2lr import build, sos
    cb = build([sos(checkerboard={"parity": 0, "pairing": "P1"}, toward=["q0", "q1"]),
                dict(type="cool", broadcast=True)])
    for rules, status in ((None, "passed"), ("c2lr", "passed"), ("c2lr-demonstrated", "failed")):
        man = dict(REP5.manifest, **({"rules": rules} if rules else {}))
        st = _Stage("rules", True)
        _rules_stage(st, TaskRelease(root=REP5.root, manifest=man), m.arch, cb)
        assert st.status == status, (rules, st.diagnostics[:2])
        assert ("ruleset" in st.detail) == bool(rules)
    assert "C6" in st.detail["failed"] and st.detail.get("assumed")


def test_the_bench_grades_the_router_on_a_board(tmp_path):
    pytest.importorskip("stim")
    from qccd.bench import board_pairs, run_suite
    from qccd.bench.harness import c2lr_compiler
    from qccd.bench.suite import with_physics
    # a C2LR device under a suite's physics keeps its steps' primitives
    doc = with_physics(Machine.c2lr_grid(3, 3).arch.to_json(), REP5.physics())
    assert doc["primitives"]["swap_or_stay"]["us"] == 312.5
    assert with_physics(Machine.grid(3, 3).arch.to_json(), REP5.physics())["primitives"] == \
        REP5.physics()["primitives"]
    # rep5_mem's physics has no C2LR primitives, so its board keeps the lattice's own
    suite, pairs = board_pairs(REP5, [Machine.c2lr_grid(5, 2).arch.to_json()])
    rep = run_suite(suite, c2lr_compiler(), workdir=tmp_path, split="none", extra_pairs=pairs, reference={})
    rec = rep["pairs"][0]
    assert rec["status"] == "valid", rec.get("reason")
    assert rec["metrics"]["transport_instructions"] > 0 and rec["ler"]["shots"] > 0


# ------------------------------------------------------------------------- the boards

C2LR_BOARDS = ["c2lr_rep5_mem_basr@1", "c2lr_rep5_mem_ybba@1", "c2lr_surface3_mem_basr@1",
               "c2lr_surface3_mem_ybba@1"]


def test_the_c2lr_boards_are_their_shuttle_boards_experiments_on_their_own_track():
    from qccd.api import _template_doc
    from qccd.arch.c2lr import c2lr_primitives
    from qccd.workspace.tasks import physics_mismatch
    for rid in C2LR_BOARDS:
        rel = find_release(rid)
        base = find_release(rel.manifest["task"].split("_", 1)[1].rsplit("_", 1)[0] + "@1")
        m, b = rel.manifest, base.manifest
        assert m["track"] == "c2lr" and m["rules"] == "c2lr" and "goal" not in m
        assert m["circuit"]["digest"] == b["circuit"]["digest"]
        assert m["qec"]["detectors"]["digest"] == b["qec"]["detectors"]["digest"]
        assert {k: m["qec"][k] for k in ("experiment", "noise", "ler")} == \
            {k: b["qec"][k] for k in ("experiment", "noise", "ler")}
        assert m["metrics"] == b["metrics"] and m["rank_by"] == b["rank_by"] == "ler_per_round"
        pair = rel.physics()["species"]["pair"]
        assert rel.physics()["primitives"]["swap_or_stay"] == c2lr_primitives(pair)["swap_or_stay"]
        # what Machine.c2lr_grid makes is entered as it is, and its suggested start is one
        st = m["starter"]
        dev = getattr(Machine, st["generator"])(**st["params"], name=st["name"])
        assert physics_mismatch(dev.arch.to_json(), rel) == []
        assert physics_mismatch(_template_doc(None), rel) == ["primitives", "species"]


def test_the_service_serves_the_track_and_no_goal_ranks_it(tmp_path):
    from qccd.official.service import OfficialError, OfficialService
    from qccd.workspace.tasks import RELEASES_DIR
    svc = OfficialService(f"sqlite:///{tmp_path / 'o.db'}", RELEASES_DIR, tmp_path / "artifacts")
    t = svc.track_board("c2lr")
    assert [b["release"] for b in t["boards"]] == C2LR_BOARDS
    assert all(b["rules"] == "c2lr" and b["track"] == "c2lr" and b["rows"] == [] for b in t["boards"])
    for goal in svc.GOALS:
        assert not set(C2LR_BOARDS) & {r.id for r in svc.goal_releases(goal)}
    with pytest.raises(OfficialError):
        svc.track_board("nope")
    lb = pytest.importorskip("qccd.site.boards")
    assert all(lb._c2lr(find_release(r)) for r in C2LR_BOARDS)
    assert 'id="c2lr"' in lb.c2lr_section([]) and "c2lr_rep5_mem_basr" not in lb.memory_section([])
