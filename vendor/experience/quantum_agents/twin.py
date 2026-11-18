"""Atomic, measured-data-only steps over the supplied quantam virtual backend.

This adapter changes the virtual control recipe, never the hidden device truth.
It preserves the supplied source and its documented model limitations.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Mapping

from .runtime import Budget, ContractError, Runtime, StepSpec, candidate_hash

ROOT = Path(__file__).resolve().parents[1]
BOUND_IDS = ('ro_resonator_survey','sq_spectroscopy','sq_rabi_time','sq_t1','sq_ramsey')
POINTS = {'ro_resonator_survey':121,'sq_spectroscopy':121,'sq_rabi_time':161,'sq_t1':81,'sq_ramsey':322}

def _plain(value):
    if isinstance(value, dict): return {str(k):_plain(v) for k,v in value.items()}
    if isinstance(value, (tuple,list)): return [_plain(v) for v in value]
    if isinstance(value, complex): return {'real':value.real,'imag':value.imag}
    if hasattr(value, 'tolist'): return _plain(value.tolist())
    return value

def _save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(_plain(value),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    temporary.replace(path)

def load_physics():
    source = ROOT / 'resources/twin_source'
    if not (source/'quantum_twin/physics.py').is_file():
        raise ContractError('随包quantam源码缺失')
    if str(source) not in sys.path: sys.path.insert(0,str(source))
    try:
        import numpy as np
        from quantum_twin.physics import Device,Qubit,Resonator,DigitalTwin,Segment,Sequence
        from quantum_twin.backend import VirtualBackend,Acquisition,request_dict
        from quantum_twin.analysis import fit_resonator,fit_spectroscopy,fit_oscillation,fit_t1,fit_ramsey
    except ImportError as error:
        raise ContractError('物理孪生需要requirements-twin.txt声明的numpy/scipy；控制平面仍可独立运行') from error
    return locals()

class QuantumTwinBackend:
    data_kind = 'simulated'

    def __init__(self, directory, *, seed=20261024, progress=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True,exist_ok=True)
        self.seed = int(seed)
        self.progress = progress or (lambda value:None)
        self.api = load_physics()
        self.config = json.loads((ROOT/'resources/twin_source/configs/p0.json').read_text(encoding='utf-8'))
        import scipy
        frozen={'schema_version':1,'seed':self.seed,'config_sha256':hashlib.sha256((ROOT/'resources/twin_source/configs/p0.json').read_bytes()).hexdigest(),
                'source_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'resources/twin_source/quantum_twin').glob('*.py'))},
                'adapter_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'python':list(sys.version_info[:3]),'numpy':self.api['np'].__version__,'scipy':scipy.__version__}
        manifest=self.directory/'frozen_manifest.json'
        if manifest.exists():
            if json.loads(manifest.read_text(encoding='utf-8'))!=frozen:raise ContractError('BACKEND_VERSION_CONFLICT: seed、配置、源码或数值环境改变；请使用新工作目录')
        elif any(self.directory.iterdir()):raise ContractError('旧后端目录缺冻结身份；请使用新工作目录')
        else:_save(manifest,frozen)
        cfg = self.config['device']; a=self.api
        self._device = a['Device'](tuple(a['Qubit'](**q) for q in cfg['qubits']),a['Resonator'](**cfg['resonator']),cfg['coupling_hz'])
        self._step_specs = {}

    def capabilities(self, step):
        base = step.id.split(':')[0]
        if base not in BOUND_IDS:
            raise ContractError(f'UNSUPPORTED_CAPABILITY: {step.id}; 已有操作卡不等于已绑定执行器')
        self._step_specs[step.id] = step
        return {'schema_version':1,'data_kind':'simulated',
                'operations':['measure','apply_trial','verify','rollback','reconcile'],
                'readback':True,'rollback':True,'reconcile':True,
                'acquisition_counts':{'measure':POINTS[base],'verify':POINTS[base]+(4 if base=='sq_rabi_time' else 0)}}

    def _path(self, operation_id):
        return self.directory/'receipts'/(hashlib.sha256(operation_id.encode()).hexdigest()+'.json')

    def _call(self, request, kind, build):
        path=self._path(request.operation_id)
        fingerprint=hashlib.sha256(json.dumps(asdict(request),sort_keys=True,allow_nan=False).encode()).hexdigest()
        if path.exists():
            old=json.loads(path.read_text(encoding='utf-8'))
            if old['request_hash']!=fingerprint: raise ContractError('IDEMPOTENCY_CONFLICT')
            return old['result']
        started=path.with_suffix('.started.json')
        if started.exists(): raise ContractError('UNKNOWN_OPERATION: 必须先核对旧操作')
        _save(started,{'operation_id':request.operation_id,'request_hash':fingerprint,'kind':kind,'started_at':time.time()})
        result=build()
        _save(path,{'operation_id':request.operation_id,'request_hash':fingerprint,'kind':kind,'result':result})
        started.unlink()
        return result

    def reconcile(self, operation_id):
        path=self._path(operation_id)
        if path.exists():return {'status':'completed','result':json.loads(path.read_text(encoding='utf-8'))['result']}
        return {'status':'unknown' if path.with_suffix('.started.json').exists() else 'not_started'}

    def _control_state(self):
        p=self.directory/'virtual_control_recipe.json'
        return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}

    def apply_trial(self, request, candidate):
        def build():
            state=self._control_state();state.update(candidate)
            _save(self.directory/'virtual_control_recipe.json',state)
            actual=self._control_state()
            return {'status':'applied','actual_values':{k:actual[k] for k in candidate}}
        return self._call(request,'apply_trial',build)

    def rollback(self, request, snapshot):
        def build():
            state=self._control_state()
            for k,v in snapshot.items():
                if v is None:state.pop(k,None)
                else:state[k]=v
            _save(self.directory/'virtual_control_recipe.json',state)
            actual=self._control_state()
            return {'status':'restored','actual_values':{k:actual.get(k) for k in snapshot}}
        return self._call(request,'rollback',build)

    def measure(self, request):
        return self._call(request,'measure',lambda:self._scan(request,None))

    def verify(self, request, candidate):
        def build():
            actual=self._control_state()
            if any(actual.get(k)!=v for k,v in candidate.items()):raise ContractError('虚拟控制参数回读不一致')
            return self._scan(request,candidate)
        return self._call(request,'verify',build)

    def _scan(self, request, proposed):
        a=self.api;np=a['np'];base,*parts=request.step_id.split(':')
        q=int(parts[0][1:]) if parts else 0
        if q not in (0,1):raise ContractError('P0仅支持q0和q1')
        variant=parts[1] if len(parts)>1 else ''
        prefix=f'q{q}.';values=dict(request.parameter_values)
        # Selection and verification get separate seeds, jobs and serialized data.
        digest=int(hashlib.sha256(request.operation_id.encode()).hexdigest()[:12],16)
        backend=a['VirtualBackend'](a['DigitalTwin'](self._device),seed=(self.seed+digest)%(2**32),**self.config['measurement'])
        records=[];start=time.monotonic();frame=[5.0e9,5.08e9][:q+1];anchors={}
        expected_points=POINTS[base]+(4 if proposed is not None and base=='sq_rabi_time' else 0)
        def required(key):
            value=values.get(key)
            if value is None or not np.isfinite(value):raise ContractError('缺前置参数 '+key)
            return float(value)
        def drive(t,amplitude=.2,phase=0.):
            amplitudes=[0.]*(q+1);phases=[0.]*(q+1)
            amplitudes[q]=amplitude;phases[q]=phase
            return a['Segment'](float(t),tuple(amplitudes),tuple(phases))
        def acquire(x,segments=None,frequency=None,quadrature=None):
            if time.time()>request.deadline:raise TimeoutError('扫描到达冻结deadline')
            if frequency is not None:
                acq=a['Acquisition']('resonator',request.shots,probe_hz=float(frequency))
            else:
                acq=a['Acquisition']('qubit',request.shots,a['Sequence'](tuple(frame),tuple(segments),request.step_id))
            measurement=backend.fetch(backend.submit(acq))
            if measurement.shots!=request.shots:raise ContractError('shots mismatch')
            if measurement.counts and (sum(measurement.counts.values())!=request.shots or any(type(v) is not int or v<0 for v in measurement.counts.values())):
                raise ContractError('invalid counts')
            records.append({'x':float(x),'quadrature':quadrature,'request':a['request_dict'](acq),'measurement':measurement.to_dict()})
            if len(records)%40==0:
                elapsed=max(time.monotonic()-start,1e-9)
                self.progress({'step':request.step_id,'phase':'verify' if proposed is not None else 'measure','points':len(records),'total':expected_points,'points_per_second':len(records)/elapsed})
            return measurement.iq_mean[0] if frequency is not None else measurement.population(q)

        if base=='ro_resonator_survey':
            center=6.5e9 if proposed is None else proposed['readout.frequency_hz']
            x=np.linspace(center-8e6,center+8e6,121)
            y=np.array([acquire(f,frequency=f) for f in x]);fit=a['fit_resonator'](x,y)
            candidate={'readout.frequency_hz':fit.parameters['frequency_hz']};tolerances={'readout.frequency_hz':50000.}
        elif base=='sq_spectroscopy':
            center=[5.0e9,5.08e9][q] if proposed is None else proposed[prefix+'frequency_coarse_hz']
            x=np.linspace(center-8e6,center+8e6,121);y=[]
            for f in x:
                frame[q]=float(f);y.append(acquire(f,[drive(250e-6,.025)]))
            fit=a['fit_spectroscopy'](x,y)
            candidate={prefix+'frequency_coarse_hz':fit.parameters['frequency_hz']};tolerances={prefix+'frequency_coarse_hz':50000.}
        elif base=='sq_rabi_time':
            seed=variant=='seed';frame[q]=required(prefix+('frequency_coarse_hz' if seed else 'frequency_hz'))
            x=np.linspace(0,3e-6,161);y=[acquire(t,[drive(t)]) for t in x]
            fit=a['fit_oscillation'](x,y);frequency=fit.parameters['frequency_hz']
            pi_key=prefix+('pi_seed_duration_s' if seed else 'pi_duration_s')
            gain_key=prefix+('rabi_seed_hz_per_amp' if seed else 'rabi_hz_per_amp')
            candidate={pi_key:1/(2*frequency),gain_key:frequency/.2}
            tolerances={k:abs(proposed[k] if proposed is not None else v)*.03 for k,v in candidate.items()}
            if proposed is not None:
                for name,factor in [('zero_pi_population',0.),('half_pi_population',.5),('pi_population',1.),('two_pi_population',2.)]:
                    duration=proposed[pi_key]*factor
                    anchors[name]=acquire(duration,[drive(duration)],quadrature='anchor_'+name)
        elif base=='sq_t1':
            frame[q]=required(prefix+'frequency_hz');pi=required(prefix+'pi_duration_s')
            x=np.linspace(0,150e-6,81);y=[acquire(t,[drive(pi),a['Segment'](float(t))]) for t in x]
            fit=a['fit_t1'](x,y)
            candidate={prefix+'t1_s':fit.parameters['t1_s']};tolerances={prefix+'t1_s':(proposed or candidate)[prefix+'t1_s']*.15}
        elif base=='sq_ramsey':
            center=required(prefix+'frequency_coarse_hz') if proposed is None else proposed[prefix+'frequency_hz']
            frame[q]=center+4e5;pi=required(prefix+'pi_seed_duration_s')
            x=np.linspace(0,40e-6,161);px=[];py=[]
            for t in x:
                for phase,arr,quad in [(0.,px,'x'),(np.pi/2,py,'y')]:
                    arr.append(acquire(t,[drive(pi/2),a['Segment'](float(t)),drive(pi/2,phase=phase)],quadrature=quad))
            fit=a['fit_ramsey'](x,px,py);y=(2*np.array(px)-1)-1j*(2*np.array(py)-1)
            candidate={prefix+'frequency_hz':frame[q]+fit.parameters['detuning_hz'],prefix+'t2_s':fit.parameters['t2_s']}
            tolerances={prefix+'frequency_hz':30000.,prefix+'t2_s':(proposed or candidate)[prefix+'t2_s']*.20}
        else:raise ContractError('UNSUPPORTED_CAPABILITY '+base)
        if not fit.success or not np.isfinite(fit.rmse):raise ContractError('无效拟合')
        for value in list(fit.parameters.values())+list(fit.stderr.values()):
            if not np.isfinite(value):raise ContractError('非有限拟合参数或不确定度')
        agreement=max(abs(candidate[k]-proposed[k])/tolerances[k] for k in candidate) if proposed is not None else 0.
        if len(records)!=expected_points:raise ContractError('扫描数据不完整')
        metrics={'fit_rmse':float(fit.rmse),'agreement_ratio':float(agreement),'fit_success':1.0,**anchors}
        result={'evidence_id':request.operation_id+'/evidence','acquisition_id':request.operation_id+'/scan',
                'data_kind':'simulated','parameter_versions':request.parameter_versions,'shots':request.shots,
                'acquisition_count':len(records),'total_shots':len(records)*request.shots,'metrics':metrics,
                'candidate':candidate,'fit':fit.to_dict(),'verification_tolerances':tolerances,'elapsed_s':time.monotonic()-start,
                'note':'新运行的quantam虚拟采集；固定IQ代理模型；非实机、非RB/XEB；一致性门限为本仿真profile示例。'}
        if proposed is not None:result.update(passed=bool(fit.rmse<.05 and agreement<=1),candidate_hash=candidate_hash(proposed))
        artifact=self.directory/'scans'/(hashlib.sha256(request.operation_id.encode()).hexdigest()+'.json')
        _save(artifact,{'request':asdict(request),'result':result,'axis':x,'observed':y,'predicted':fit.predicted,'records':records})
        result['artifact_path']=str(artifact.relative_to(self.directory))
        result['artifact_sha256']=hashlib.sha256(artifact.read_bytes()).hexdigest()
        self.progress({'step':request.step_id,'phase':'verify' if proposed is not None else 'measure','points':len(records),'total':len(records),'points_per_second':len(records)/max(time.monotonic()-start,1e-9),'rmse':fit.rmse})
        return _plain(result)

def twin_steps(qubits=(0,),shots=2048):
    budget=Budget(shots=shots,max_attempts=2,timeout_s=180,max_total_shots=4*322*shots,max_acquisitions=1288)
    criteria={'fit_rmse':{'max':.05},'agreement_ratio':{'max':1.},'fit_success':{'min':1.}}
    rabi_criteria={**criteria,'zero_pi_population':{'max':.1},'half_pi_population':{'min':.4,'max':.6},'pi_population':{'min':.9},'two_pi_population':{'max':.1}}
    steps=[StepSpec('ro_resonator_survey',writes=('readout.frequency_hz',),resources=('readout_bus',),budget=budget,criteria=criteria)]
    for q in qubits:
        prefix=f'q{q}.';suffix=f':q{q}'
        spec='sq_spectroscopy'+suffix;seed='sq_rabi_time'+suffix+':seed';ramsey='sq_ramsey'+suffix;fine='sq_rabi_time'+suffix+':final';t1='sq_t1'+suffix
        common={'resources':(f'q{q}','readout_bus'),'budget':budget,'criteria':criteria}
        steps.extend([
            StepSpec(spec,depends_on=('ro_resonator_survey',),writes=(prefix+'frequency_coarse_hz',),**common),
            StepSpec(seed,depends_on=(spec,),reads=(prefix+'frequency_coarse_hz',),writes=(prefix+'pi_seed_duration_s',prefix+'rabi_seed_hz_per_amp'),**{**common,'criteria':rabi_criteria}),
            StepSpec(ramsey,depends_on=(seed,),reads=(prefix+'frequency_coarse_hz',prefix+'pi_seed_duration_s'),writes=(prefix+'frequency_hz',prefix+'t2_s'),**common),
            StepSpec(fine,depends_on=(ramsey,),reads=(prefix+'frequency_hz',),writes=(prefix+'pi_duration_s',prefix+'rabi_hz_per_amp'),**{**common,'criteria':rabi_criteria}),
            StepSpec(t1,depends_on=(fine,),reads=(prefix+'frequency_hz',prefix+'pi_duration_s'),writes=(prefix+'t1_s',),**common)])
    return steps

def run_twin(directory, *, qubits=(0,), shots=2048, seed=20261024, operation_id='twin-workflow', progress=None):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    backend=QuantumTwinBackend(directory/'backend',seed=seed,progress=progress)
    runtime=Runtime(directory/'runtime.sqlite',twin_steps(qubits,shots))
    try:
        runs=runtime.run_workflow(backend,operation_id)
        result={'data_kind':'simulated','operation_id':operation_id,'runs':runs,'parameters':runtime.get_parameters(),
                'valid_evidence':{step:runtime.evidence(step) is not None for step in runtime.steps},
                'source':'resources/twin_source','limitation':'仅5类原子物理绑定；新频率后重测Rabi。单腔读出代理模型不验证真实读出训练。'}
        _save(directory/'twin_summary.json',result)
        return result
    finally:runtime.close()
