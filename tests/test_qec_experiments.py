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
