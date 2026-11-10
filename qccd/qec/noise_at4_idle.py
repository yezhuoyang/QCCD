"""Deterministic D1 events for @4, never mutable eval-language state.

Formula/policies: qccd-noise-model bca3de9 src/qccd_noise/engine.py:108-121,379-430.
Source-operation policy: PR A user decision; CNOT target follows steane-qccd
src/qccd_presets.py:151-161,194-201 (neither CNOT operand resets).
"""
import math
from collections import defaultdict
from .native import quarter_turns
from ..evalset.model import Op, OPERATED
from ..evalset.words import word

T_LIN = 183.28657935856594
T_G = 3.7948765959083626

def idle_increment(age, dt):
    if dt <= 0:
        return 0.0
    a, b = max(age, 0.0), max(age, 0.0) + dt
    return .5 * (1 - math.exp(-(b-a)/T_LIN-(b*b-a*a)/(T_G*T_G)))

def phase_reset_operands(name, qubits, params=()):
    """Source net-action rule, with the explicitly approved CNOT-target exception."""
    name = name.upper()
    if name in ('I','Z','S','S_DAG','VZ','CZ','ZZ','CX','CNOT','SWAP','BARRIER'):
        return ()
    if name in ('H','X','Y','R','M','PREPARE','RESET') and not params:
        # R without parameters is the source parser's reset spelling.
        return tuple(qubits)
    if name in ('RX','RY','RXY','R','MS','RXX','XX'):
        if not params:
            raise ValueError(f'D1 source {name} needs explicit angles')
        turns = quarter_turns(params[0], 'source pulse angle')
        if name in ('RXY','R'):
            if len(params)!=2:
                raise ValueError('D1 R(theta,phi) needs two source angles')
            quarter_turns(params[1], 'source pulse phase')
        return tuple(qubits) if turns else ()
    if name in ('RZ','RZZ'):
        if not params:
            raise ValueError(f'D1 source {name} needs an angle')
        quarter_turns(params[0], 'source Z angle')
        return ()
    raise ValueError(f'D1 source gate {name!r} has no approved phase policy')

@word('at4_idle_intervals', 'a set of operations', 'Precomputed @4 D1 boundary events.', 'execution', domain=True)
def _intervals(c):
    if 'at4_idle_intervals' not in c.run.facts:
        raise ValueError('Run was not prepared for the @4 idle policy')
    return c.run.facts['at4_idle_intervals']

@word('at4_idle_probability(o)', 'a probability', 'Full-precision D1 increment before event o.', 'state')
def _probability(c, o):
    return c.run.facts['at4_idle_probabilities'][o.index]

def _metadata(run, source):
    meta = run.experiment.phase_metadata
    if not isinstance(meta, dict) or set(meta)!= {'measurements','round_boundaries'}:
        raise ValueError('D1 requires unambiguous phase_metadata: measurements and round_boundaries')
    records = meta['measurements']
    if not isinstance(records, dict):
        raise ValueError('D1 measurements must be keyed by source measurement bit')
    expected={str(o.bit):o for o in source.ops if o.name=='M'}
    expected.update({str(b):None for _,b,_ in run.experiment.readout_plan()})
    if set(records)!=set(expected):
        raise ValueError('D1 measurement sidecar is missing or has ambiguous source bits')
    program_bits=[o.bit for o in source.ops if o.name=='M']
    if len(program_bits)!=len(set(program_bits)):
        raise ValueError('D1 source reuses a measurement bit ambiguously')
    for bit,row in records.items():
        if not isinstance(row,dict) or set(row)!= {'check_id','role','pauli_type','data_support','round','qubit'}:
            raise ValueError(f'D1 malformed measurement metadata for bit {bit}')
        if row['role'] not in ('syndrome','flag','data') or row['pauli_type'] not in ('X','Z'):
            raise ValueError(f'D1 invalid role/Pauli type for bit {bit}')
        support=row['data_support']
        if not isinstance(support,list) or len(support)!=len(set(support)) or any(type(q)!=int or not 0<=q<run.experiment.n_data for q in support):
            raise ValueError(f'D1 invalid data support for bit {bit}')
        if row['role']=='syndrome' and (not support or not row['check_id']):
            raise ValueError(f'D1 syndrome bit {bit} lacks a check identity/support')
        if type(row['round'])!=int or not 0<=row['round']<run.experiment.rounds:
            raise ValueError(f'D1 invalid round for bit {bit}')
        if type(row['qubit'])!=int or row['qubit'] not in run.qubit_map or not isinstance(row['check_id'],str):
            raise ValueError(f'D1 invalid source qubit/check identity for bit {bit}')
        op=expected[bit]
        if op is not None and row['qubit']!=op.qubits[0]:
            raise ValueError(f'D1 source measurement qubit disagrees for bit {bit}')
    for q,b,basis in run.experiment.readout_plan():
        row=records[str(b)]
        if row['qubit']!=q or row['role']!='data' or row['pauli_type']!=basis:
            raise ValueError('D1 appended-readout sidecar disagrees with the experiment')
    boundaries=meta['round_boundaries']
    if not isinstance(boundaries,list) or len(boundaries)!=run.experiment.rounds:
        raise ValueError('D1 requires one explicit round boundary per logical round')
    for r,boundary in enumerate(boundaries):
        want={int(b) for b,row in records.items() if row['round']==r and row['role']!='data'}
        if not isinstance(boundary,dict) or boundary.get('round')!=r or set(boundary)!= {'round','after_bits'} or not want or set(boundary['after_bits'])!=want or len(boundary['after_bits'])!=len(want):
            raise ValueError(f'D1 ambiguous round boundary {r}')
    return records,boundaries

def prepare(run, policy, source):
    """Add zero-translation anchors; other noise keeps its original native gap partition."""
    if policy=='per_interval':
        run.facts['at4_idle_intervals']=[]
        return
    if policy not in ('since_reset','whole_round'):
        raise ValueError('Unknown D1 policy')
    records,boundaries=_metadata(run,source)
    qmap=run.qubit_map
    inverse={ion:q for q,ion in qmap.items()}
    ends={};measure_ends={};completion=defaultdict(list)
    # Every physical pulse is attributed to an ORIGINAL source gate, by operand.
    for st in run.steps:
        native=[o for o in st.ops if o.kind in OPERATED]
        if not native:
            continue
        ids=(st.instr.meta or {}).get('source_op')
        if not isinstance(ids,list) or not ids or len(ids)!=len(set(ids)) or any(type(i)!=int or not 0<=i<len(source.ops) for i in ids):
            raise ValueError(f'D1 instruction {st.instr.id} lacks unambiguous source-gate provenance')
        used=set()
        for o in native:
            for ion in o.ions:
                if ion not in inverse:
                    raise ValueError('D1 native operation has no source-qubit mapping')
                q=inverse[ion]
                matches=[i for i in ids if q in source.ops[i].qubits]
                if len(matches)!=1:
                    raise ValueError(f'D1 ambiguous source gate on {ion} at instruction {st.instr.id}')
                sid=matches[0];src=source.ops[sid]
                if (o.kind=='measure')!=(src.name=='M') or (o.kind=='reset')!=(src.name=='R' and not src.params):
                    raise ValueError('D1 native kind disagrees with source provenance')
                used.add(sid);ends[sid]=st
        if used!=set(ids):
            raise ValueError('D1 instruction carries source provenance for untouched operands')
    for sid,src in enumerate(source.ops):
        reset=phase_reset_operands(src.name,src.qubits,src.params)
        if src.name=='BARRIER':
            continue
        if sid not in ends:
            if reset or src.name in ('M','SWAP'):
                raise ValueError(f'D1 source operation {sid} has no physical completion')
            continue
        st=ends[sid];completion[st.index].append((sid,src,reset))
        if src.name=='M':
            measure_ends[src.bit]=st.index
    round_ends=defaultdict(list)
    for b in boundaries:
        if any(bit not in measure_ends for bit in b['after_bits']):
            raise ValueError('D1 round boundary has no mapped source measurement')
        round_ends[max(measure_ends[bit] for bit in b['after_bits'])].append(b['round'])
    ordered=[r for idx in sorted(round_ends) for r in round_ends[idx]]
    if ordered!=list(range(run.experiment.rounds)) or any(len(rs)!=1 for rs in round_ends.values()):
        raise ValueError('D1 round boundaries are ambiguous or reordered')
    ages={i:0. for i in run.ions};pending=dict(ages)
    intervals=[];probabilities={};event_log=[]
    next_id=max((o.index for o in run.ops),default=-1)+1
    def flush(ion,st,dest,why):
        nonlocal next_id
        dt=pending[ion]
        if dt>0:
            op=Op(next_id,st,'at4_idle',(ion,));next_id+=1
            probabilities[op.index]=idle_increment(ages[ion],dt)
            event_log.append({'ion':ion,'step':st.index,'when':why,'age_s':ages[ion],'dt_s':dt,'p':probabilities[op.index]})
            ages[ion]+=dt;pending[ion]=0.
            intervals.append(op);dest.append(op)
    def reset(ions,st,after,why):
        ions=list(ions)
        if ions and policy=="since_reset":
            event_log.append({"phase_reset":ions,"step":st.index,"when":why})
        for ion in ions:
            flush(ion,st,after,why)
            if policy=='since_reset':ages[ion]=0.
    for st in run.all_steps:
        original=list(st.ops);before=[];after=[]
        touched={i for o in original if o.kind in OPERATED for i in o.ions}
        for ion in sorted(touched):flush(ion,st,before,'before_native')
        for ion in pending:
            if st.in_programme and ion not in touched:
                pending[ion]+=st.duration*1e-6
        if st.kind=='prepare':
            ages=dict.fromkeys(run.ions,0.);pending=dict(ages)
        for sid,src,reset_q in sorted(completion.get(st.index,()),key=lambda x:x[0]):
            if src.name=='SWAP':
                a,b=(qmap[q] for q in src.qubits)
                flush(a,st,after,'before_state_swap');flush(b,st,after,'before_state_swap')
                prior=[ages[a],ages[b]]
                ages[a],ages[b]=ages[b],ages[a]
                event_log.append({'state_swap':[a,b],'step':st.index,'before_ages_s':prior,'after_ages_s':[ages[a],ages[b]]})
            else:
                reset([qmap[q] for q in reset_q],st,after,f'source_completion:{sid}:{src.name}')
            if src.name=='M':
                row=records[str(src.bit)]
                if row['role']=='syndrome' and row['pauli_type']=='X':
                    reset([qmap[q] for q in row['data_support']],st,after,'x_stabilizer_measurement')
        if st.kind=='readout':
            reset(sorted(touched),st,after,'appended_measurement')
        if st.index in round_ends and policy=='whole_round':
            for ion in sorted(pending):
                flush(ion,st,after,'round_boundary');ages[ion]=0.
        st.ops=before+original+after
    run.ops.extend(intervals)
    run.facts['at4_idle_intervals']=intervals
    run.facts['at4_idle_probabilities']=probabilities
    run.facts['at4_idle_events']=event_log
    run.facts['at4_idle_final_ages_s']=ages
