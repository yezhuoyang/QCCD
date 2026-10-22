"""Memory experiments: the source circuits a logical error rate is measured on.

What must hold before any compiler is involved:

* every registered experiment's source circuit is deterministic and its detectors and
  observables have parity 0 (they are defined that way);
* the circuit-level distance of the source under uniform noise equals the code distance
  -- the schedule is part of the experiment, and a hook-unsafe one would silently halve
  what a board measures.  Steane needs its flags for this, which is checked too;
* the QASM stays inside the subset qccdc parses, and an appended readout leaves the data
  measurements out without renumbering a single detector bit.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

stim = pytest.importorskip("stim")

from qccd.qec import (EXPERIMENTS, MemoryExperiment, bb, get_experiment,  # noqa: E402
                      ideal_stim, noiseless_check, repetition, rotated_surface, steane)
from qccd.qec.experiments import rotated_surface_xz, steane_code  # noqa: E402
from qccd.qec.qasm import QasmError, parse_qasm  # noqa: E402

SEARCH = dict(dont_explore_detection_event_sets_with_size_above=4,
              dont_explore_edges_with_degree_above=4,
              dont_explore_edges_increasing_symptom_degree=False,
              canonicalize_circuit_errors=True)


def circuit_distance(exp) -> int:
    return len(ideal_stim(exp, 1e-3).search_for_undetectable_logical_errors(**SEARCH))


@pytest.mark.parametrize("key", sorted(EXPERIMENTS))
def test_every_registered_source_passes_the_noiseless_check(key):
    exp = get_experiment(key)
    res = noiseless_check(ideal_stim(exp, 0.0))
    assert res.ok, res.reason
    assert exp.readout == "appended"
    assert len(exp.readout_bits) == exp.n_data


@pytest.mark.parametrize("key", sorted(EXPERIMENTS))
def test_spec_round_trips_through_json(key):
    exp = get_experiment(key)
    spec = json.loads(json.dumps(exp.spec()))
    assert spec["kind"] == "qccd.detectors" and spec["version"] == 1
    assert MemoryExperiment.from_spec(spec, exp.qasm) == exp


@pytest.mark.parametrize("key", sorted(EXPERIMENTS))
def test_qasm_uses_only_the_subset(key):
    exp = get_experiment(key)
    lines = [ln for ln in exp.qasm.splitlines() if ln and not ln.startswith("//")]
    assert lines[:2] == ["OPENQASM 2.0;", 'include "qelib1.inc";']
    words = {ln.split()[0] for ln in lines[2:]}
    assert words <= {"qreg", "creg", "h", "cx", "measure", "reset"}, words
    prog = parse_qasm(exp.qasm)
    assert prog.n_qubits == exp.n_data + exp.n_ancilla


@pytest.mark.parametrize("make,d", [
    (lambda: repetition(3), 3),
    (lambda: repetition(5), 5),
    (lambda: rotated_surface(3), 3),
    (lambda: rotated_surface(5), 5),
    (lambda: steane(3), 3),
    (lambda: rotated_surface(3, basis="X"), 3),
    (lambda: steane(3, basis="X"), 3),
    (lambda: rotated_surface(3, readout="program"), 3),
])
def test_circuit_distance_is_the_code_distance(make, d):
    exp = make()
    assert exp.distance == d
    assert circuit_distance(exp) == d


@pytest.mark.parametrize("readout", ["appended", "program"])
def test_both_bases_side_by_side_keep_the_distance(readout):
    exp = rotated_surface_xz(3, readout=readout)
    assert exp.basis == "XZ" and exp.n_data == 18 and len(exp.observables) == 2
    assert circuit_distance(exp) == 3
    if readout == "appended":
        assert [s for _, _, s in exp.readout_plan()] == ["Z"] * 9 + ["X"] * 9


def test_each_patch_sees_its_own_error_type():
    """A logical X on the Z patch flips observable 0 only; a logical Z on the X patch flips
    observable 1 only; neither fires a detector.  So the pair sees bit flips AND dephasing."""
    from qccd.gadget.surface import patch

    exp = rotated_surface_xz(3)
    p = patch(3, 3)
    lines = str(ideal_stim(exp, 0.0)).splitlines()
    # data qubit 0 is first measured by the appended Z readout, the X patch by the first MX
    at_m = next(i for i, ln in enumerate(lines) if ln.startswith("M ") and "0" in ln.split()[1:])
    at_mx = next(i for i, ln in enumerate(lines) if ln.startswith("MX "))
    # as error channels: the sampler reports flips against the noiseless reference, which a
    # plain X gate would be part of
    for flip, at, want in (("X_ERROR(1) " + " ".join(map(str, p.logical("X"))), at_m, [True, False]),
                           ("Z_ERROR(1) " + " ".join(str(q + 9) for q in p.logical("Z")), at_mx,
                            [False, True])):
        c = stim.Circuit("\n".join(lines[:at] + [flip] + lines[at:]))
        det, obs = c.compile_detector_sampler().sample(8, separate_observables=True)
        assert not det.any() and (obs == want).all(), flip


def test_steane_needs_its_flags():
    """A bare-ancilla Steane round has distance 2: a hook X_aX_b plus X on the third point
    of their Fano line.  That is why the registered experiment carries flags."""
    assert circuit_distance(steane(3, flags=False)) == 2


def test_appended_readout_changes_the_circuit_not_the_detectors():
    app = rotated_surface(3, readout="appended")
    prog = rotated_surface(3, readout="program")
    assert app.detectors == prog.detectors and app.observables == prog.observables
    assert prog.qasm.count("measure") - app.qasm.count("measure") == 9
    assert parse_qasm(app.qasm).n_bits == parse_qasm(prog.qasm).n_bits
    assert dict(app.readout_bits) == {d: 3 * 8 + d for d in range(9)}
    assert noiseless_check(ideal_stim(prog, 0.0)).ok
    assert prog.spec()["readout"] == "program" and "readout_bits" not in prog.spec()


def test_matchable_is_read_off_the_checks():
    assert repetition(3).matchable and rotated_surface(3).matchable
    assert not steane(3).matchable and not bb("bb18", rounds=1).matchable


def test_bb72_builds_with_twelve_observables():
    exp = get_experiment("bb72")
    c = ideal_stim(exp, 0.0)
    assert c.num_observables == 12 and c.num_qubits == 144
    assert noiseless_check(c).ok


def test_the_parser_refuses_what_it_does_not_know():
    with pytest.raises(QasmError):
        parse_qasm('OPENQASM 2.0;\nqreg q[1];\nt q[0];\n')
    with pytest.raises(QasmError):
        parse_qasm('OPENQASM 2.0;\nqreg q[1];\nrz(0.1) q[0];\n')
    with pytest.raises(QasmError):
        parse_qasm('OPENQASM 2.0;\nqreg q[1];\nh q[3];\n')


def _qccdc():
    from qccd.qec import _toolchain

    qccdc, _, _ = _toolchain()
    if qccdc is None:
        pytest.skip("qccdc_cli is not built")
    return qccdc


PROGRAM_READOUT = {
    "rep3_program": lambda: repetition(3, readout="program"),
    "surface3_program": lambda: rotated_surface(3, readout="program"),
    "steane_program": lambda: steane(3, readout="program"),
}


@pytest.mark.parametrize("key", sorted(EXPERIMENTS) + sorted(PROGRAM_READOUT))
def test_qccdc_parses_the_qasm(key, tmp_path):
    qccdc = _qccdc()
    exp = PROGRAM_READOUT[key]() if key in PROGRAM_READOUT else get_experiment(key)
    p = tmp_path / f"{key}.qasm"
    p.write_text(exp.qasm)
    cp = subprocess.run([str(qccdc), "parse", str(p)], capture_output=True, text=True,
                        timeout=60)
    assert cp.returncode == 0, cp.stdout[-500:] + cp.stderr[-500:]


def test_logical_qubits_count_each_qubit_once_across_both_bases():
    """Goal 1 divides a rate per round by k: a two-basis memory tests each logical qubit twice."""
    from qccd.qec.experiments import logical_qubits
    assert logical_qubits(rotated_surface_xz(3)) == 1 and logical_qubits(rotated_surface_xz(5)) == 1
    assert logical_qubits(rotated_surface(3)) == 1 and logical_qubits(get_experiment("bb72")) == 12
    s = rotated_surface_xz(5).summary()
    assert s["logical_qubits"] == 1 and s["ions"] == 98


@pytest.mark.parametrize("key,d", [("steanexz", 3), ("shorxz", 2), ("rep3xz", 1), ("bb18xz", 4)])
def test_every_small_code_in_both_bases_has_its_stated_circuit_distance(key, d):
    """Goal 1's other codes: what each schedule really protects (Shor's single flag cannot save a
    weight-6 check, so 2; a repetition code's X memory has none, so 1)."""
    exp = get_experiment(key)
    assert exp.basis == "XZ" and exp.distance == d
    assert circuit_distance(exp) == d


def test_one_block_in_both_bases_measures_z_then_x():
    """The sequential form (bb72, bb90 under 200 ions): phase 1 is read out in the program, every
    ion is reset, phase 2 is appended in X; 2k observables, k logical qubits, one phase's rounds."""
    from qccd.qec.experiments import css_memory_seq
    exp = css_memory_seq(steane_code(), 3, "steaneseq", distance=3, flags=True)
    assert exp.basis == "XZ" and exp.logical_qubits == 1 and len(exp.observables) == 2
    assert exp.rounds == 3 and exp.n_data == 7
    assert [s for _, _, s in exp.readout_plan()] == ["X"] * 7
    assert exp.qasm.count("measure q[0]") == 1          # phase 1's data readout, in the program
    assert noiseless_check(ideal_stim(exp, 0.0)).ok
    assert circuit_distance(exp) == 3


@pytest.mark.parametrize("key,k,ions", [("bb72seq", 12, 144), ("bb90seq", 8, 180)])
def test_the_large_bivariate_bicycle_codes_fit_goal_one_in_one_block(key, k, ions):
    """What the bb72seq / bb90seq boards are ranked on: k logical qubits, both bases, under 200 ions."""
    exp = get_experiment(key)
    s = exp.summary()
    assert exp.basis == "XZ" and exp.logical_qubits == k and len(exp.observables) == 2 * k
    assert s["ions"] == ions <= 200 and s["logical_qubits"] == k
