"""The tiled surface compiler (qccd/compile/tiled_surface.py): a rotated surface patch's rounds on
a grid, every plaquette playing the same moves at once.

Held here:

* at every distance tried the programme passes every rule (under the default physics and under
  qccd-physics@2) and R10's checks that do not need the Lean checker -- the certificate's
  binding, transport, witnesses and per-qubit order, and the stabilizer tableau composed from
  the emitted pulses against the source; where the Lean checker is built, it accepts too;
* the programme is the same instructions at every distance (one round: 39), so writing it is
  linear in the ions, and the round takes as long at d = 9 as at d = 3;
* any set of faces plays, with the data where the experiment's `coords` put them: lattice
  surgeries -- the merged pair, and FormalRV's CNOT (`surgery_cnot`: three patches, two
  phases) -- pass every rule and R10, keep circuit distance d, and their logical error rate
  under qccd-noise@2 falls with d;
* it refuses a circuit that is not a rotated surface patch's, saying why.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from qccd.compile.cooling import insert_cooling
from qccd.compile.tiled_surface import TiledError, compile_tiled, device
from qccd.cost.models import corrected_model
from qccd.qec.experiments import rotated_surface
from qccd.verify import verify

from test_c2lr_router import BRIDGE, _r10


@pytest.mark.parametrize("d, rounds", [(3, 1), (5, 3), (9, 2)])
def test_every_rule_and_r10_hold_at_every_distance(d, rounds, tmp_path):
    exp = rotated_surface(d, rounds)
    prog, cert, arch = compile_tiled(exp.qasm, d)
    for physics, table in ((None, "qccdsim_jones"), ("physics@2", "quantinuum")):
        a = device(d, physics=physics)
        model = corrected_model(table)
        rep = verify(insert_cooling(prog, a, model).program, a, model, check_metrics=False)
        assert rep.rules.summary().get("failed") == [], rep.rules.violations[:3]
    _r10(prog, cert, arch, exp.qasm, tmp_path)


def _rectangle(rows, cols, rounds):
    """Syndrome rounds on a rows x cols patch: a lattice surgery's merged patch."""
    from qccd.gadget.surface import patch
    from qccd.qec.experiments import _Check, _memory
    p = patch(rows, cols)
    checks = [_Check(c.basis, tuple(c.support), tuple(c.layers))
              for c in sorted(p.checks, key=lambda c: (c.basis != "X", int(c.name[1:])))]
    return _memory(f"rect{rows}x{cols}", f"surface_{rows}x{cols}", min(rows, cols), p.n, checks,
                   [p.logical("Z")], rounds, "Z", layered=True)


@pytest.mark.parametrize("rows, cols", [(7, 3), (3, 7)])
def test_a_lattice_surgerys_merged_patch_is_played_the_same_way(rows, cols, tmp_path):
    """The Z-bar Z-bar merge of two d = 3 patches is 7 x 3, the X-bar X-bar one 3 x 7."""
    exp = _rectangle(rows, cols, 2)
    prog, cert, arch = compile_tiled(exp.qasm, rows, cols)
    model = corrected_model("qccdsim_jones")
    rep = verify(insert_cooling(prog, arch, model).program, arch, model, check_metrics=False)
    assert rep.rules.summary().get("failed") == [], rep.rules.violations[:3]
    _r10(prog, cert, arch, exp.qasm, tmp_path)


@pytest.mark.parametrize("kind", ["ZZ", "XX"])
def test_a_lattice_surgery_measures_the_product_and_keeps_both_logicals(kind, tmp_path):
    """`lattice_surgery`: noiseless, every detector and all three observables (the outcome,
    and each patch's logical) are deterministic -- so the merge measures Z-bar Z-bar (X-bar
    X-bar) and disturbs neither logical; tiled, the programme passes every rule under
    qccd-physics@2 and R10."""
    from qccd.qec.experiments import ideal_stim, lattice_surgery
    exp = lattice_surgery(3, kind)
    c = ideal_stim(exp)
    c.detector_error_model()                 # raises on a random detector or observable
    assert not c.compile_detector_sampler().sample(32, append_observables=True).any()
    rows, cols = (7, 3) if kind == "ZZ" else (3, 7)
    a = device(rows, cols, physics="physics@2")
    prog, cert, _ = compile_tiled(exp.qasm, rows, cols, arch=a)
    model = corrected_model("quantinuum")
    rep = verify(insert_cooling(prog, a, model).program, a, model, check_metrics=False)
    assert rep.rules.summary().get("failed") == [], rep.rules.violations[:3]
    _r10(prog, cert, a, exp.qasm, tmp_path)


@pytest.mark.parametrize("gadget", ["ZZ", "cnot"])
def test_a_surgerys_logical_error_rate_falls_with_the_distance(gadget):
    from qccd.qec import evaluate_memory
    from qccd.qec.experiments import lattice_surgery, surgery_cnot
    model = corrected_model("quantinuum")
    ler = {}
    for d in (3, 5):
        exp = lattice_surgery(d, "ZZ") if gadget == "ZZ" else surgery_cnot(d, "Z")
        rows, cols = (2 * d + 1, d) if gadget == "ZZ" else (2 * d + 1, 2 * d + 1)
        a = device(rows, cols, physics="physics@2")
        prog, cert, _ = compile_tiled(exp.qasm, rows, cols, arch=a, coords=exp.coords)
        cooled = insert_cooling(prog, a, model).program
        cooled.meta.update(prog.meta)
        rep = evaluate_memory(cooled, a.to_json(), exp, noise_id="qccd-noise@2", cert=cert, seed=7,
                              ler_budget={"max_errors": 40, "max_shots": 1_000_000})
        assert rep["check"]["ok"], rep["check"]
        ler[d] = rep["ler"]["ler"]
    assert ler[5] < ler[3] / 3, ler


@pytest.mark.parametrize("basis", ["Z", "X"])
def test_a_surgery_cnot_makes_its_flow_at_full_distance(basis, tmp_path):
    """`surgery_cnot`: noiseless, every detector and both observables are deterministic --
    observable 0 is the flow the CNOT makes (Z̄_T -> Z̄_C Z̄_T, or X̄_C -> X̄_C X̄_T, with
    FormalRV's corrections), observable 1 what it keeps; under uniform circuit noise the
    smallest graphlike logical error has d faults; tiled on one lattice -- C above A, T right
    of A, two phases -- the programme passes every rule under qccd-physics@2 and R10."""
    from qccd.qec.experiments import ideal_stim, surgery_cnot
    for d in (3, 5):
        exp = surgery_cnot(d, basis)
        c = ideal_stim(exp)
        c.detector_error_model()
        assert not c.compile_detector_sampler().sample(32, append_observables=True).any()
        assert len(ideal_stim(exp, 1e-3).shortest_graphlike_error(ignore_ungraphlike_errors=True)) == d
    exp = surgery_cnot(3, basis)
    a = device(7, 7, physics="physics@2")
    prog, cert, _ = compile_tiled(exp.qasm, 7, 7, arch=a, coords=exp.coords)
    model = corrected_model("quantinuum")
    rep = verify(insert_cooling(prog, a, model).program, a, model, check_metrics=False)
    assert rep.rules.summary().get("failed") == [], rep.rules.violations[:3]
    _r10(prog, cert, a, exp.qasm, tmp_path)


def test_a_surgery_cnots_flow_needs_its_correction():
    """Observable 0 without the Z̄Z̄ outcome m1 is random: the experiment sees the gate."""
    from dataclasses import replace

    from qccd.qec.experiments import ideal_stim, surgery_cnot
    exp = surgery_cnot(3, "Z")
    flow = set(exp.observables[0])
    m1 = flow & set(range(min(b for _, b in exp.readout_bits)))     # the bits measured in the programme
    assert m1
    broken = replace(exp, observables=(tuple(sorted(flow - m1)),) + exp.observables[1:])
    with pytest.raises(ValueError):
        ideal_stim(broken).detector_error_model()


def test_the_programme_is_the_same_instructions_at_every_distance():
    lens, rounds_us = set(), set()
    model = corrected_model("qccdsim_jones")
    for d in (3, 5, 9):
        prog, _, arch = compile_tiled(rotated_surface(d, 1).qasm, d)
        lens.add(len(prog.instructions))
        rep = verify(insert_cooling(prog, arch, model).program, arch, model, check_metrics=False)
        rounds_us.add(round(rep.result.total_us, 3))
    assert lens == {39} and len(rounds_us) == 1, (lens, rounds_us)


def test_the_proved_checker_accepts_the_certificate(tmp_path):
    exe = os.environ.get("QCCD_QCHECK")
    if not exe or not Path(exe).is_file():
        from test_c2lr import _qcheck
        exe = _qcheck()
    if exe is None:
        pytest.skip("the Lean checker (qcheck) is not built here")
    exp = rotated_surface(5, 2)
    prog, cert, arch = compile_tiled(exp.qasm, 5)
    arch_p = tmp_path / "dev.arch.json"
    arch_p.write_text(json.dumps(arch.to_json()), encoding="utf-8")
    subprocess.run([sys.executable, str(BRIDGE / "export_arch.py"), str(arch_p), "-o",
                    str(tmp_path / "dev.expanded.json")], check=True, capture_output=True)
    prog.save(str(tmp_path / "prog.tsir.json"), indent=None)
    (tmp_path / "prog.qcert.json").write_text(json.dumps(cert), encoding="utf-8")
    subprocess.run([sys.executable, str(BRIDGE / "mk_qcheck_input.py"), str(tmp_path / "prog"), "--arch",
                    str(tmp_path / "dev.expanded.json"), "-o", str(tmp_path / "q.json")],
                   check=True, capture_output=True)
    out = subprocess.run([str(exe), str(tmp_path / "q.json")], capture_output=True, text=True, timeout=600)
    assert "ACCEPTED" in out.stdout, out.stdout + out.stderr


def test_a_circuit_that_is_not_a_surface_patch_is_refused_saying_why():
    exp = rotated_surface(3, 1)
    bad = exp.qasm.replace("cx q[10],q[1];", "cx q[10],q[8];", 1)
    with pytest.raises(TiledError, match="not the corners of one face of the 3 x 3 lattice"):
        compile_tiled(bad, 3)
    with pytest.raises(TiledError, match="not between a data ion and a measure ion"):
        compile_tiled(exp.qasm.replace("cx q[10],q[1];", "cx q[0],q[1];", 1), 3)
