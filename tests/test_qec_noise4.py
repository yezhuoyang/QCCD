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
    ev=load('qccd-noise@4').with_params({'idle_policy':'per_interval'});e=ev.evaluator(ev.run(prog,arch,experiment=exp,cert=cert))
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

def active_experiment(name):
    from qccd.qec import rotated_surface, steane
    return rotated_surface(3,rounds=3) if name=='surface3' else steane(rounds=3)

def test_d1_metadata_roundtrip_and_strict_missing(frozen_case):
    from dataclasses import replace
    name,prog,arch,old,cert=frozen_case
    exp=active_experiment(name)
    assert exp.qasm==old.qasm
    assert MemoryExperiment.from_spec(exp.spec(),exp.qasm).phase_metadata==exp.phase_metadata
    model=NoiseModel4(config=replace(OFF,idle_policy='since_reset'))
    with pytest.raises(ValueError,match='phase_metadata'):
        extract(prog,arch,old,noise=model,cert=cert)
    ex=extract(prog,arch,exp,noise=model,cert=cert)
    base=extract(prog,arch,old,noise=NoiseModel4(config=OFF),cert=cert)
    def without_idle(ex):return [(o,l) for o,l in zip(ex.origin,ex.lines) if o not in ('idle_dephasing','idle_dephasing_d1')]
    assert without_idle(ex)==without_idle(base)
    assert ex.budget['idle_leakage']==base.budget['idle_leakage']
    assert ex.stats['t_us']==base.stats['t_us']
    assert ex.stats['rules_failed']==[]
    bad=replace(exp,phase_metadata={'measurements':{},'round_boundaries':[]})
    with pytest.raises(ValueError,match='sidecar'):
        extract(prog,arch,bad,noise=model,cert=cert)

@pytest.mark.parametrize('name,params,expected',[
    ('H',(),(0,)),('X',(),(0,)),('Y',(),(0,)),('Z',(),()),
    ('S',(),()),('S_DAG',(),()),('VZ',(1.5707963267948966,),()),
    ('CX',(),()),('CZ',(),()),('ZZ',(),()),('SWAP',(),()),
    ('RX',(0.,),()),('RY',(2*np.pi,),()),('RX',(np.pi,), (0,)),
    ('RXY',(np.pi/2,np.pi/2),(0,)),('MS',(np.pi/2,),(0,)),
    ('MS',(0.,),()),('MS',(2*np.pi,),()),('RZ',(np.pi,),()),
    ('RZZ',(np.pi,),()),('R',(),(0,)),('M',(),(0,)),
])
def test_source_phase_rule(name,params,expected):
    from qccd.qec.noise_at4_idle import phase_reset_operands
    assert phase_reset_operands(name,(0,),params)==expected
    if name in ('CX','MS','SWAP'):
        assert phase_reset_operands(name,(0,1),params)==((0,1) if expected else ())

def test_d1_channel_composition_and_linear_hazard():
    from qccd.qec.noise_at4_idle import idle_increment,T_LIN,T_G
    import math
    age=0.;keep=1.
    for dt in [.004,.0001,.0041,.0009,.004]*7:
        keep*=1-2*idle_increment(age,dt);age+=dt
    assert (1-keep)/2==pytest.approx(.5*(1-math.exp(-age/T_LIN-(age/T_G)**2)),abs=1e-15)
    assert idle_increment(0,-1)==0
    assert idle_increment(.05,.01)>idle_increment(0,.01)

def test_d1_uses_source_provenance_and_flushes_x_measurement(frozen_case):
    from qccd.qec.noise import evaluation
    from dataclasses import replace
    name,prog,arch,_,cert=frozen_case
    exp=active_experiment(name)
    ev=evaluation(NoiseModel4(config=replace(OFF,idle_policy='since_reset')))
    run=ev.run(prog,arch,experiment=exp,cert=cert)
    logs=run.facts['at4_idle_events']
    assert any(e.get('when')=='x_stabilizer_measurement' and 'dt_s' in e for e in logs)
    assert any(e.get('when','').endswith(':H') and e.get('phase_reset') for e in logs)
    assert not any(e.get('when','').endswith(':CX') and e.get('phase_reset') for e in logs)
    assert all(age==0 for age in run.facts['at4_idle_final_ages_s'].values())
    # Event anchors at the end of the step follow its real measurement operation.
    for st in run.steps:
        synthetic=[o for o in st.ops if o.kind=='at4_idle']
        if any(e.get('step')==st.index and e.get('when')=='x_stabilizer_measurement' and 'dt_s' in e for e in logs):
            assert synthetic
            assert st.ops[-1].kind=='at4_idle'
    damaged=TSIR.from_json(prog.to_json())
    from dataclasses import replace as dc_replace
    damaged.instructions=[dc_replace(i,meta={k:v for k,v in i.meta.items() if k!='source_op'}) if i.type=='gate' else i for i in damaged.instructions]
    with pytest.raises(ValueError,match='provenance'):
        ev.run(damaged,arch,experiment=exp,cert=cert)

def test_whole_round_uses_explicit_boundaries(frozen_case):
    from qccd.qec.noise import evaluation
    from dataclasses import replace
    name,prog,arch,_,cert=frozen_case
    exp=active_experiment(name)
    ev=evaluation(NoiseModel4(config=replace(OFF,idle_policy='whole_round')))
    run=ev.run(prog,arch,experiment=exp,cert=cert)
    logs=run.facts['at4_idle_events']
    assert len({e['step'] for e in logs if e.get('when')=='round_boundary'})==exp.rounds
    assert not any(e.get('phase_reset') for e in logs)
    bad=replace(exp,phase_metadata={**exp.phase_metadata,'round_boundaries':[]})
    with pytest.raises(ValueError,match='round boundary'):
        ev.run(prog,arch,experiment=bad,cert=cert)

def test_source_parser_keeps_barriers_and_angles():
    from qccd.qec.noise_at4_source import parse_source
    src=parse_source('OPENQASM 2.0; qreg q[2]; creg c[1]; h q[0]; barrier q; rx(2*pi) q[0]; ms(pi/2) q[0],q[1]; measure q[0] -> c[0];',keep_barriers=True)
    assert [o.name for o in src.ops]==['H','BARRIER','RX','MS','M']
    assert src.ops[2].params==(2*np.pi,)
    assert src.ops[-1].bit==0

def test_d1_gate_swap_moves_age_and_transport_does_not():
    from types import SimpleNamespace
    from qccd.evalset.model import Run,Step,Op
    from qccd.qec.qasm import QasmProgram,QasmOp
    from qccd.qec.noise_at4_idle import prepare
    prep=Step(-1,None,'prepare');prep.in_programme=False
    source=QasmProgram(2,1,[QasmOp('H',(0,)),QasmOp('SWAP',(0,1)),QasmOp('M',(1,),0)])
    rows={'0':dict(check_id='z',role='syndrome',pauli_type='Z',data_support=[0],round=0,qubit=1)}
    exp=MemoryExperiment('swap','synthetic',1,1,'Z',1,1,'',(),(),phase_metadata={'measurements':rows,'round_boundaries':[{'round':0,'after_bits':[0]}]})
    steps=[];ops=[]
    for k,(kind,us,sid,ions,gate) in enumerate([
        ('simd',1000,None,(),None),('gate',5,0,('a',),'H'),
        ('gate',70,1,('a','b'),'SWAP'),('simd',2000,None,(),None),
        ('measure',150,2,('b',),None)]):
        ins=SimpleNamespace(id=k,meta={} if sid is None else {'source_op':[sid]})
        st=Step(k,ins,kind);st.dt=us
        if sid is not None:
            op=Op(len(ops),st,'measure' if kind=='measure' else 'gate2' if len(ions)==2 else 'gate1',ions,gate=gate)
            st.ops=[op];ops.append(op)
        steps.append(st)
    run=Run(None,None,'helios',None,None,['a','b'],steps,prep,None,ops,experiment=exp,qubit_map={0:'a',1:'b'})
    prepare(run,'since_reset',source)
    swap=next(e for e in run.facts['at4_idle_events'] if 'state_swap' in e)
    assert swap['before_ages_s']==pytest.approx([0,.001005])
    assert swap['after_ages_s']==pytest.approx([.001005,0])
    # The later transport accrues on b, which now carries a's freshly reset state.
    measurement=next(e for e in run.facts['at4_idle_events'] if e.get('ion')=='b' and e['step']==4)
    assert measurement['age_s']==0
    assert measurement['dt_s']==pytest.approx(.002)

def test_d1_missing_or_ambiguous_native_angles_fail():
    from qccd.qec.noise_at4_idle import phase_reset_operands
    from qccd.qec.noise_at4_source import parse_source
    with pytest.raises(ValueError,match='angles'):phase_reset_operands('MS',(0,1))
    with pytest.raises(ValueError):phase_reset_operands('T',(0,))
    with pytest.raises(ValueError):parse_source('qreg q[1]; rx(__import__("os")) q[0];')

def test_d1_cannot_silently_emit_from_an_unprepared_run(frozen_case):
    from qccd.evalset import load
    from qccd.qec import noise_at4_idle  # register the pure lookup words
    _,prog,arch,exp,cert=frozen_case
    old_run=load("qccd-noise@3").run(prog,arch,experiment=exp,cert=cert)
    with pytest.raises(ValueError,match="not prepared"):
        load("qccd-noise@4").circuit(old_run)


