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
* H̄ (`hadamard_bar`, Gehér et al. 2024 on the machine's lattice): noiselessly the gate's flow
  (Z̄ -> X̄, X̄ -> Z̄) is deterministic, every deterministic parity is a declared detector, the
  circuit keeps distance d; tiled -- both checkerboards, data states moved through measure ions
  -- it passes every rule and R10, and its logical error rate falls with d;
* a corridor merge (`corridor_merge`: any patches, any corridor of free tiles) measures its
  product, keeps every patch's logical and keeps distance d; tiled, every rule and R10;
* the Shor machine (`qccd.fullstack.scale.machine`): a round at its distance on its physics
  (qccd-physics-best) passes every rule and R10;
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


@pytest.mark.parametrize("gadget", ["ZZ", "cnot", "hbar"])
def test_a_surgerys_logical_error_rate_falls_with_the_distance(gadget):
    from qccd.qec import evaluate_memory
    from qccd.qec.experiments import hadamard_bar, lattice_surgery, surgery_cnot
    model = corrected_model("quantinuum")
    ler = {}
    for d in (3, 5):
        exp, (rows, cols) = {"ZZ": (lattice_surgery(d, "ZZ"), (2 * d + 1, d)),
                             "cnot": (surgery_cnot(d, "Z"), (2 * d + 1, 2 * d + 1)),
                             "hbar": (hadamard_bar(d, "Z"), (d, 2 * d + 1))}[gadget]
        a = device(rows, cols, physics="physics@2")
        prog, cert, _ = compile_tiled(exp.qasm, rows, cols, arch=a, coords=exp.coords,
                                      faces=exp.faces)
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


@pytest.mark.parametrize("basis", ["Z", "X"])
def test_h_bar_exchanges_the_logicals_at_full_distance(basis):
    """`hadamard_bar`: noiselessly every detector and the flow (Z̄ -> X̄ from |0̄>, X̄ -> Z̄ from
    |+̄>, with the corrections stim's flow solver finds) are deterministic; at d = 3 every
    deterministic parity of the measurements is a declared detector or the flow (none left out,
    which an observable could otherwise pick up as a fault no detector sees); under uniform
    circuit noise the smallest graphlike logical error has d faults."""
    from dataclasses import replace

    from qccd.gadget import gf2
    from qccd.qec.experiments import hadamard_bar, ideal_stim
    for d in (3, 5):
        exp = hadamard_bar(d, basis)
        c = ideal_stim(exp)
        c.detector_error_model()
        assert not c.compile_detector_sampler().sample(32, append_observables=True).any()
        assert len(ideal_stim(exp, 1e-3).shortest_graphlike_error(ignore_ungraphlike_errors=True)) == d
    exp = hadamard_bar(3, basis)
    bare = ideal_stim(replace(exp, detectors=(), observables=()))
    m = bare.num_measurements
    shots = bare.compile_sampler().sample(4 * m + 64)
    random_part = gf2.rank(int("".join("1" if b else "0" for b in reversed(s ^ shots[0])), 2)
                           for s in shots)
    order = _measured_bits(exp.qasm) + [b for _, b in exp.readout_bits]
    at = {b: k for k, b in enumerate(order)}
    declared = gf2.rank(sum(1 << at[b] for b in bits) for bits in exp.detectors + exp.observables)
    assert declared == m - random_part


def _measured_bits(qasm: str) -> list[int]:
    from qccd.qec.qasm import parse_qasm
    return [op.bit for op in parse_qasm(qasm).ops if op.name == "M"]


def test_h_bar_is_played_on_the_tiled_grid(tmp_path):
    """Tiled on the d x (2d + 1) of a patch, its seam and the neighbouring tile -- the deformed
    patches on the other checkerboard, every data state moved one column through a measure ion
    -- H̄ passes every rule under qccd-physics@2 and R10."""
    from qccd.qec.experiments import hadamard_bar
    exp = hadamard_bar(3, "Z")
    a = device(3, 7, physics="physics@2")
    prog, cert, _ = compile_tiled(exp.qasm, 3, 7, arch=a, coords=exp.coords, faces=exp.faces)
    model = corrected_model("quantinuum")
    rep = verify(insert_cooling(prog, a, model).program, a, model, check_metrics=False)
    assert rep.rules.summary().get("failed") == [], rep.rules.violations[:3]
    _r10(prog, cert, a, exp.qasm, tmp_path)


CORRIDORS = {
    "U": ("Z", [((0, 0), "bottom"), ((2, 0), "bottom")], {(0, 1), (1, 1), (2, 1)}),
    "bend": ("Z", [((1, 0), "bottom"), ((0, 3), "top")], {(1, 1), (0, 2), (1, 2)}),
    "three": ("Z", [((0, 0), "bottom"), ((2, 0), "bottom"), ((4, 0), "bottom")],
              {(x, 1) for x in range(5)}),
    "fan-out": ("X", [((0, 0), "right"), ((0, 2), "right"), ((2, 1), "left")],
                {(1, 0), (1, 1), (1, 2)}),
    "far": ("X", [((0, 0), "right"), ((4, 2), "left")], {(1, 0), (1, 1), (2, 1), (3, 1), (3, 2)}),
}


@pytest.mark.parametrize("shape", sorted(CORRIDORS))
def test_a_corridor_merge_measures_the_product_and_keeps_every_logical(shape):
    """`corridor_merge`: noiselessly the product and every patch's own logical are
    deterministic (so the merged region holds one logical qubit fewer than the patches), and the
    circuit keeps distance d -- for a U, a bend either way, three patches in a row, a fan-out
    column and a long X̄X̄ path."""
    from qccd.qec.experiments import corridor_merge, ideal_stim
    letter, patches, corridor = CORRIDORS[shape]
    for d in (3, 5):
        exp = corridor_merge(d, letter, patches, corridor)
        c = ideal_stim(exp)
        c.detector_error_model()
        assert not c.compile_detector_sampler().sample(32, append_observables=True).any()
        assert len(ideal_stim(exp, 1e-3).shortest_graphlike_error(ignore_ungraphlike_errors=True)) == d


@pytest.mark.parametrize("shape", ["bend", "fan-out"])
def test_a_corridor_merge_is_played_on_the_tiled_grid(shape, tmp_path):
    from qccd.qec.experiments import corridor_merge
    letter, patches, corridor = CORRIDORS[shape]
    exp = corridor_merge(3, letter, patches, corridor)
    rows, cols = (max(x[k] for x in exp.coords) + 1 for k in (0, 1))
    a = device(rows, cols, physics="physics@2")
    prog, cert, _ = compile_tiled(exp.qasm, rows, cols, arch=a, coords=exp.coords, faces=exp.faces)
    model = corrected_model("quantinuum")
    rep = verify(insert_cooling(prog, a, model).program, a, model, check_metrics=False)
    assert rep.rules.summary().get("failed") == [], rep.rules.violations[:3]
    _r10(prog, cert, a, exp.qasm, tmp_path)


def test_the_shor_machines_round_holds_at_its_distance_on_its_physics(tmp_path):
    """`qccd.fullstack.scale.machine`: a syndrome round at the machine's distance on
    qccd-physics-best passes every rule and R10; that physics is qccd-physics@2 but for the
    fidelities of the two-qubit gate, the one-ion pulse and the reset, each with its source."""
    from qccd.fullstack.scale import machine as M
    d = M.DISTANCE
    exp = rotated_surface(d, 1)
    a = M.device(d)
    prog, cert, _ = compile_tiled(exp.qasm, d, arch=a)
    model = corrected_model("quantinuum")
    rep = verify(insert_cooling(prog, a, model).program, a, model, check_metrics=False)
    assert rep.rules.summary().get("failed") == [], rep.rules.violations[:3]
    _r10(prog, cert, a, exp.qasm, tmp_path)
    phys = Path(__file__).resolve().parents[1] / "qccd" / "workspace" / "physics"
    best, ref = (json.loads((phys / f).read_text(encoding="utf-8"))["primitives"]
                 for f in ("qccd-physics-best.json", "qccd-physics@2.json"))
    changed = {k for k in ref if best[k] != ref[k]}
    assert changed == {"ms_gate", "1q_gate", "reset"}
    for k in changed:
        assert best[k]["source"].startswith("arXiv:") and best[k]["source"] != ref[k]["source"]
        assert {f for f in ref[k] if best[k][f] != ref[k][f]} <= {
            "fidelity", "fidelity_at_n0", "error_vs_quanta", "error", "note", "source"}


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
