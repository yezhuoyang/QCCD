"""Frozen bc282b5 @3 output and opt-in PR A mechanisms."""
import json
from pathlib import Path
import numpy as np
import pytest
import stim
from qccd.arch import Architecture
from qccd.ir.tsir import TSIR
from qccd.qec.experiments import MemoryExperiment
from qccd.qec.extract import extract
from qccd.qec.noise import NoiseModel3, NoiseModel4, NoiseAt4Config

FIXTURE = Path(__file__).parent/'fixtures/noise_at3'
OFF = NoiseAt4Config(thermal_gate_error='linear', idle_policy='per_interval', meas_crosstalk_scope='chip')

@pytest.fixture(scope='module', params=['surface3','steane'])
def frozen_case(request):
    row=json.loads((FIXTURE/f'{request.param}.json').read_text())
    return request.param, TSIR.from_json(row['program']), Architecture.from_json(json.loads((FIXTURE/'device.json').read_text())), MemoryExperiment.from_spec(row['experiment'],row['qasm']),row['certificate']

def test_frozen_at3_and_at4_all_off(frozen_case):
    name,prog,arch,exp,cert=frozen_case
    meta=json.loads((FIXTURE/'manifest.json').read_text())
    assert stim.__version__==meta['stim'], 'Golden samples require the Stage 0 Stim version'
    golden=stim.Circuit((FIXTURE/f'{name}.stim').read_text())
    for model in (NoiseModel3(),NoiseModel4(config=OFF)):
        ex=extract(prog,arch,exp,noise=model,cert=cert)
        assert str(ex.circuit)==str(golden)
        samples=ex.circuit.compile_detector_sampler(seed=meta['seed']).sample(meta['shots'],append_observables=True)
        assert list(samples.shape)==meta['cases'][name]['sample_shape']
        assert np.packbits(samples,bitorder='little').tobytes().hex()==meta['cases'][name]['sample_bits_hex']
        assert ex.stats['rules_failed']==[]

def test_config_contains_only_pr_a_switches():
    from dataclasses import fields
    assert {f.name for f in fields(NoiseAt4Config)}=={'thermal_gate_error','idle_policy','meas_crosstalk_scope'}
    for field in ('thermal_gate_error','idle_policy','meas_crosstalk_scope'):
        with pytest.raises(ValueError):NoiseAt4Config(**{field:'unknown'})

def test_reference_thermal_law_full_precision(frozen_case):
    from qccd.evalset import load
    from qccd.evalset.syntax import parse_term
    _,prog,arch,exp,cert=frozen_case
    ev=load('qccd-noise@4');e=ev.evaluator(ev.run(prog,arch,experiment=exp,cert=cert))
    for row in json.loads((FIXTURE/'thermal_reference.json').read_text())['values']:
        actual=e.term(parse_term(f"reference_thermal({row['excess']})"),{})
        assert actual==pytest.approx(row['stim_excess'],abs=1e-12,rel=0)

def test_thermal_switch_changes_only_ms_excess(frozen_case):
    from dataclasses import replace
    _,prog,arch,exp,cert=frozen_case
    off=extract(prog,arch,exp,noise=NoiseModel4(config=OFF),cert=cert)
    on=extract(prog,arch,exp,noise=NoiseModel4(config=replace(OFF,thermal_gate_error='reference')),cert=cert)
    assert [(o,l) for o,l in zip(off.origin,off.lines) if o!='ms']==[(o,l) for o,l in zip(on.origin,on.lines) if o!='ms']
    assert {k:v for k,v in off.budget.items() if k!='ms_heating'}=={k:v for k,v in on.budget.items() if k!='ms_heating'}
    assert on.budget['ms_base']==off.budget['ms_base']
    # Cold gates have zero excess; a hot schedule changes the MS probability.
    if off.budget['ms_heating']:
        assert on.budget['ms_heating']!=off.budget['ms_heating']
