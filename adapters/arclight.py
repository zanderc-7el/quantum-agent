"""Private ArcLight execution bridge; API import and planning are hardware-free."""
from copy import deepcopy
import ast
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import time
from .f8 import AdapterError, ALIASES, F8Adapter, _dump, _load, _digest, _utc
from .history import sha256
from .progress_delivery import read_event_batch
from .arclight_runtime.platform_specs import build_job, POLICY

METHODS={
 'ro_resonator_survey':('s21','s01_readout_frequency'), 'ro_power_shift':('s21_power','s01_readout_frequency'),
 'sq_spectroscopy':('spectrum','s02_qubit_frequency'), 'sq_rabi_amplitude':('rabi_amplitude','s03_rabi_amplitude'),
 'sq_rabi_time':('rabi_time','s03_rabi_amplitude'), 'sq_rabi_frequency':('rabi_time','s03_rabi_amplitude'),
 'sq_allxy':('allxy','s03_rabi_amplitude'), 'ro_iq01':('readout','s04_readout_fidelity'),
 'ro_optimize':('readout','s05_readout_optimization'), 'ro_optimize_frequency':('readout','s05_readout_optimization'),
 'ro_s21_ge':('readout','s05_readout_optimization'), 'sq_t1':('t1','s06_t1'),
 'sq_ramsey':('ramsey','s07_t2'), 'sq_echo':('echo','s07_t2'), 'sq_cpmg':('cpmg','s07_t2'),
 'sq_rb':('rb_suite','s08_single_qubit_rb')}
CALIBRATION_KEYS=('readout_hz','readout_amp','drive_hz','drive_amp','drive_length_s','half_pi_amp','xy_delay_s',
 'xy_attenuation_db','xy_filter_mhz','readout_width_s','pulse_compatibility')
INTEGER_FIELDS={'shots','points','capture_repeats','acquisitions_per_point','pi_pulses','xy_filter_mhz'}
OPTIONS={'frequencies_hz','amplitudes','powers','start_hz','stop_hz','rb_lengths','rb_sequences','rb_master_seed','rb_bootstrap_replicates','order_seed'}
SCAN_PARAMETER_BINDINGS={
    'para.probFreq':'readout_hz','para.probCarryFreq':'readout_hz',
    'para.drivFreq':'drive_hz','para.drivCarryFreq':'drive_hz',
    'para.probCarryScale':'readout_amp','gate.U_gates.pi_amp':'drive_amp'}

def same(a,b):
    if type(a) in (int,float) and type(b) in (int,float):return math.isfinite(a) and math.isfinite(b) and math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-12)
    return type(a) is type(b) and a==b

class ArcLightAdapter(F8Adapter):
    physical_hardware=True
    CALIBRATION_KEYS=CALIBRATION_KEYS
    def __init__(self,project_root,profile):
        super().__init__(project_root,profile)
        self.operations=self.root/'data/runs/arclight_operations'
    def with_execution_enabled(self):return type(self)(self.root,{**self.profile,'enabled':True})
    def validation_parameter_keys(self):return CALIBRATION_KEYS
    def normalize_candidate(self,procedure_id,candidate):
        out=deepcopy(candidate)
        if 'drive_amp' in out:out['half_pi_amp']=out['drive_amp']
        if 'drive_length_s' in out:out['drive_length_s']=round(out['drive_length_s']*2e9)/2e9
        return out
    def normalize_parameter_changes(self,procedure_id,changes,current_parameters):
        return self.normalize_candidate(procedure_id,changes)
    def build_verification_parameters(self,procedure_id,parameters,candidate):
        """Readout verification evaluates the selected setting, not a new winner."""
        out=deepcopy(parameters)
        if procedure_id=='ro_optimize' and 'readout_amp' in candidate:
            out['amplitudes']=[candidate['readout_amp']]
        if procedure_id in ('ro_optimize_frequency','ro_s21_ge') and 'readout_hz' in candidate:
            out['frequencies_hz']=[candidate['readout_hz']]
        return out
    def hardware_ownership(self,refresh=False):
        # The generic controller/F8 helper only reads local connection ownership.
        from .f8 import F8Adapter
        result=F8Adapter.hardware_ownership(self,refresh=refresh);result['owners']=list(result.get('owners',[]))
        for value in self.profile['hardwareLocks']:
            path=Path(value)
            if path.exists():
                try:record=_load(path)
                except Exception:record={'state':'unreadable_lock; ownership unknown'}
                result['owners'].append({'kind':'chassis_lock','path':str(path),'pid':record.get('pid'),'operationId':record.get('operationId'),'state':'held_or_unreconciled'})
        return result
    def capabilities(self):
        return {'id':self.profile['id'],'dataKind':'real','enabled':self.profile.get('enabled') is True,
            'physicalHardware':True,'isolatedProcess':True,'directImportInApi':False,'supportsReconcile':True,
            'supportsCancel':'cooperative actual ADC block/point boundaries; forced termination quarantines locks',
            'resources':list(self.profile['resources']),'hardwareLease':'controller plus shared ArcLight and F8 chassis locks',
            'procedures':{k:{'available':True,'status':'conditional' if k=='sq_rb' else 'code_inspected',
                'kind':v[0],'ownerId':v[1],'entrypoint':'ArcLight Experiment.load / SequenceRunner',
                'requires':['current-run accepted independent Rabi/readout; fresh PRE/POST; paired native seeds'] if k=='sq_rb' else []} for k,v in METHODS.items()},
            'unavailable':{'double_qubit':'No flux/coupler calibration or execution bound','network_analyzer':'Current ArcLight path uses pulsed RF/ADC'}}
    def parameter_schema(self,procedure_id):
        if procedure_id not in METHODS:return {}
        return {k:({'enum':v['enum']} if isinstance(v,dict) else {'min':v[0],'max':v[1],**({'integer':True} if k in INTEGER_FIELDS else {})}) for k,v in self.profile['parameterBounds'].items()}
    def preflight(self):
        checks=[]
        def record(name,ok,detail):checks.append({'name':name,'ok':bool(ok),'detail':str(detail)})
        try:
            runner=self._local(self.profile['runner']);record('runner_hash',runner.exists() and sha256(runner)==self.profile['runnerSha256'],runner)
            for item in _load(self._local(self.profile['sourceManifest'])):
                p=self._local(item['path']);record('source:'+p.name,p.is_file() and sha256(p)==item['sha256'],p)
            ast.parse(runner.read_text(encoding='utf8'));record('syntax',True,'AST only; native imports not executed')
            interpreter=Path(self.profile['python']);record('python',interpreter.is_file(),interpreter)
            record('visa',Path(self.profile['visaLibrary']).is_file(),self.profile['visaLibrary'])
            record('resource_map',set(self.profile['resources'])=={'Q11'},'Explicit frequency-order Q11 maps to native internal Q1')
            record('routing',self.profile['instrument']['xyPhysical']==[0,0] and self.profile['instrument']['readoutPhysical']==[8,0] and self.profile['instrument']['adcPhysical']==[0,0],'Frozen tested RF0/RO8/OSC0 mapping')
            record('shared_locks',len(self.profile['hardwareLocks'])==2,self.profile['hardwareLocks'])
        except Exception as exc:record('preflight_error',False,exc)
        ready=bool(checks) and all(c['ok'] for c in checks)
        return {'ready':ready,'staticReady':ready,'enabled':self.profile.get('enabled') is True,'executionReady':ready and self.profile.get('enabled') is True,
            'hardwareConnected':False,'ownershipVerified':False,'checks':checks,'notes':['Historical working point is only a seed. No hardware I/O during preflight.']}
    def build_plan(self,request,output_dir=None):
        procedure=request.get('procedureId');resource=request.get('resourceId');operation=str(request.get('operationId',''))
        if procedure not in METHODS:raise AdapterError('UNSUPPORTED_PROCEDURE','ArcLight 未绑定方法 '+str(procedure))
        if resource not in self.profile['resources']:raise AdapterError('UNKNOWN_RESOURCE','ArcLight 当前只绑定 Q11')
        if not operation or len(operation)>256:raise AdapterError('INVALID_OPERATION_ID','Invalid operationId')
        p={k:v for k,v in self.profile['resources'][resource].items() if k in CALIBRATION_KEYS}
        p.update(shots=2000,capture_repeats=2,acquisitions_per_point=1,points=61,start_s=0.,stop_s=2.4e-6,
            span_hz=4e6,rotation_hz=8e5,amp_start=.05,amp_stop=.98,pi_pulses=4,spectroscopy_width_s=8e-6,
            order_seed=int(hashlib.sha256(operation.encode()).hexdigest()[:8],16))
        if procedure=='sq_spectroscopy':p.update(span_hz=10e6,points=81)
        if procedure in ('sq_t1','sq_cpmg','sq_ramsey','sq_echo'):p.update(stop_s=12e-6 if procedure=='sq_t1' else 5e-6 if procedure=='sq_ramsey' else 8e-6,acquisitions_per_point=4,points=61 if procedure=='sq_t1' else 81)
        if procedure=='ro_iq01':p.update(capture_repeats=6)
        if procedure in ('ro_optimize','ro_optimize_frequency','ro_s21_ge'):p.update(points=9)
        if procedure=='sq_rb':p.update(rb_lengths=[1,2,4,8,12,16,24,32],rb_sequences=20,rb_master_seed=p['order_seed'],rb_bootstrap_replicates=300,capture_repeats=1,acquisitions_per_point=4)
        for k,v in request.get('parameters',{}).items():
            name=ALIASES.get(k,k)
            if name not in self.profile['parameterBounds'] and name not in OPTIONS:raise AdapterError('UNKNOWN_PARAMETER','Unsupported '+name)
            p[name]=v
        for name,value in p.items():
            rule=self.profile['parameterBounds'].get(name)
            if isinstance(rule,dict):
                if value not in rule['enum']:raise AdapterError('INVALID_PARAMETER',name+' policy unsupported')
            elif rule:self._validate_number(name,value,rule,integer=name in INTEGER_FIELDS)
        for name in ('order_seed','rb_master_seed'):
            if name in p:self._validate_number(name,p[name],[0,2**32-1],integer=True)
        for name,field in [('frequencies_hz','drive_hz' if procedure.startswith('sq_') else 'readout_hz'),('amplitudes','drive_amp' if procedure.startswith('sq_') else 'readout_amp'),('powers','readout_amp')]:
            if name in p:
                if not isinstance(p[name],list) or not 1<=len(p[name])<=161:raise AdapterError('INVALID_PARAMETER',name)
                for x in p[name]:self._validate_number(field,x)
        for name in ('start_hz','stop_hz'):
            if name in p:self._validate_number('drive_hz',p[name])
        if p['stop_s']<=p['start_s'] or p['amp_stop']<=p['amp_start']:raise AdapterError('INVALID_SCAN','Scan endpoints must increase')
        if not same(p['half_pi_amp'],p['drive_amp']):raise AdapterError('PULSE_POLICY_MISMATCH','当前短半脉冲策略要求 π/2 与 π 同幅度，π/2 使用半时长')
        if not math.isclose(p['drive_length_s']*2e9,round(p['drive_length_s']*2e9),abs_tol=1e-6):raise AdapterError('PULSE_GRID','π/2 时长必须对齐 4GHz 样点')
        duration=p['stop_s']+(p['pi_pulses']+2)*(p['drive_length_s']+2e-9) if procedure=='sq_cpmg' else p['stop_s']+4*(p['drive_length_s']+2e-9) if procedure in ('sq_t1','sq_ramsey','sq_echo') else p['stop_s'] if procedure in ('sq_rabi_time','sq_rabi_frequency') else p['spectroscopy_width_s'] if procedure=='sq_spectroscopy' else 5*(p['drive_length_s']+2e-9)
        if duration>79.7e-6:raise AdapterError('SEQUENCE_WINDOW','Control sequence exceeds fixed 80us readout window')
        base=_load(self._local(self.profile['runtimeRoot'])/'base_config.json');name='op_'+hashlib.sha256(operation.encode()).hexdigest()[:16]
        job=build_job(p,procedure,base,name);jobs=[job];evidence=None
        endpoint_corrections=[]
        if procedure=='sq_rabi_amplitude' and not p.get('amplitudes'):
            # The frozen uniform-axis builder can overshoot a requested endpoint
            # by one ULP. Restore only its generated endpoints, after the request
            # has passed strict bounds; explicit axes and interior values stay exact.
            for axis in job['spec']['sweeps']:
                if axis['bindings']!='cfg.Q1.gate.U_gates.pi_amp':continue
                values=axis['values']
                for index,key in ((0,'amp_start'),(len(values)-1,'amp_stop')):
                    emitted,requested=values[index],p[key]
                    if emitted!=requested and abs(emitted-requested)<=2*max(math.ulp(emitted),math.ulp(requested)):
                        endpoint_corrections.append({'binding':axis['bindings'],'pointIndex':index,
                            'sourceParameter':key,'computedValue':emitted,'actualValue':requested,
                            'reason':'generated_uniform_axis_endpoint_roundoff'})
                        values[index]=requested
        if procedure=='sq_allxy':
            pre=build_job({**p,'capture_repeats':6,'acquisitions_per_point':2},'ro_iq01',base,name+'_reference')
            pre['rb_role']='independent_reference';jobs=[pre,job]
        if procedure=='sq_rb':
            self._validate_number('rb_sequences',p['rb_sequences'],[20,40],integer=True)
            self._validate_number('rb_bootstrap_replicates',p['rb_bootstrap_replicates'],[100,2000],integer=True)
            lengths=p['rb_lengths']
            if not isinstance(lengths,list) or not 6<=len(lengths)<=10 or lengths!=sorted(set(lengths)) or any(type(x)is not int or not 1<=x<=32 for x in lengths):raise AdapterError('RB_LENGTHS_INVALID','RB lengths invalid')
            if (4*max(lengths)+3)*(p['drive_length_s']+2e-9)>79.7e-6:raise AdapterError('RB_SEQUENCE_WINDOW','RB bound exceeds fixed readout timing')
            evidence=self._validate_snapshot(request,p)
            pre=build_job({**p,'capture_repeats':6,'acquisitions_per_point':2},'ro_iq01',base,name+'_pre');pre['rb_role']='pre'
            ref=deepcopy(job);ref.update(name=name+'_rb',rb_role='reference')
            irb=deepcopy(job);irb.update(name=name+'_irb',kind='rb_interleaved',interleaved_gate='X/2',rb_role='interleaved')
            post=deepcopy(pre);post.update(name=name+'_post',rb_role='post')
            jobs=[pre,ref,irb,post]
        rows=[]
        for j in jobs:
            self._validate_scan_axes(j)
            typ=j['spec']['meta']['type'];sweeps=j['spec']['sweeps']
            n=21 if typ=='4' else len(p['rb_lengths'])*p['rb_sequences'] if typ=='3' else (len(sweeps[0]['values']) if j['spec']['meta']['expand_mode']=='zip' else math.prod(len(a['values']) for a in sweeps))
            rows.append(n*j['captures'])
        total=sum(n*j['spec']['meta']['shots']*j['acquisitions_per_point'] for n,j in zip(rows,jobs))
        if sum(rows)>self.profile['maximumPoints'] or total>self.profile['maximumTotalShots']:raise AdapterError('BUDGET_EXCEEDED','ArcLight acquisition budget exceeded')
        return {'version':1,'adapter':'arclight','operationId':operation,'procedureId':procedure,'resourceId':resource,'parameters':p,'jobs':jobs,
            'expectedJobRows':rows,'totalRows':sum(rows),'estimated_total_shots':total,'max_seconds':min(self.profile['maximumRunSeconds'],float(request.get('maxSeconds',self.profile['maximumRunSeconds']))),
            'validationEvidence':evidence,'requestIdentity':{k:request.get(k) for k in ('runId','nodeId','attemptId','operationId','resourceId','procedureId','parameterVersions')},
            'hardwareLocks':self.profile['hardwareLocks'],'instrument':self.profile['instrument'],
            **({'derivedAxisEndpointCorrections':endpoint_corrections} if endpoint_corrections else {})}
    def _validate_scan_axes(self,job):
        """Validate emitted physical axes, including values derived from center/span."""
        for axis_index,axis in enumerate(job['spec']['sweeps']):
            binding=axis['bindings']
            if not isinstance(binding,str) or not binding.startswith('cfg.'):continue
            if job['kind']=='rabi_time' and binding.endswith('.gate.U_gates.pi_len'):
                # TimeRabi emits one X (not X/2): the frozen native compiler
                # checks width * XY rate with half=False. Do not round this axis;
                # its recorded uniform coordinates must remain the actual plan.
                rate=job['config'][binding.split('.')[1]]['gate']['sRate']['XY']
                for point_index,value in enumerate(axis['values']):
                    samples=value*rate if type(value) in (int,float) else None
                    if (samples is None or not math.isfinite(samples) or value<0
                            or not math.isclose(samples,round(samples),rel_tol=0,abs_tol=1e-7)):
                        raise AdapterError('PULSE_GRID',
                            f'TimeRabi X 脉冲扫描轴 {binding}[{point_index}] 未对齐 {1/rate:g} s 样点；起点及步长必须对齐，未取整或下发',
                            {'job':job['name'],'axis':axis['name'],'binding':binding,
                             'axisIndex':axis_index,'pointIndex':point_index,'parameter':'pi_len',
                             'value':value,'sampleRateHz':rate,'sampleQuantumSeconds':1/rate,
                             'sampleCount':samples,'nativeHalfRotation':False})
            field=SCAN_PARAMETER_BINDINGS.get('.'.join(binding.split('.')[2:]))
            if field is None:continue
            for point_index,value in enumerate(axis['values']):
                try:self._validate_number(field,value)
                except AdapterError as exc:
                    raise AdapterError(exc.code,f'ArcLight 扫描轴 {binding}[{point_index}]: {exc}',
                        {**exc.details,'job':job['name'],'axis':axis['name'],'binding':binding,
                         'axisIndex':axis_index,'pointIndex':point_index,'parameter':field,'value':value}) from exc
    def _validate_snapshot(self,request,p):
        snap=request.get('validationSnapshot')
        if not isinstance(snap,dict) or snap.get('version')!=1:raise AdapterError('RB_VALIDATION_REQUIRED','RB requires current independently validated calibration')
        if any(snap.get(k)!=request.get(k) for k in ('runId','resourceId')):raise AdapterError('RB_VALIDATION_IDENTITY','Snapshot identity mismatch')
        for k in CALIBRATION_KEYS:
            if not same(snap.get('parameters',{}).get(k),p[k]) or snap.get('parameterVersions',{}).get(k)!=request.get('parameterVersions',{}).get(k) or type(request.get('parameterVersions',{}).get(k)) is not int:raise AdapterError('RB_STALE_CALIBRATION','Calibration mismatch '+k)
        categories=set();proof=[];seen=set()
        for entry in snap.get('acceptedEvidence',[]):
            proc=entry.get('procedureId');category='rabi' if proc in ('sq_rabi_time','sq_rabi_amplitude') else 'readout' if proc in ('ro_iq01','ro_optimize','ro_optimize_frequency') else None
            if not category:continue
            ops=[entry.get('selectionOperationId'),entry.get('verificationOperationId')]
            if entry.get('status')!='passed' or not entry.get('ruleVersion') or not all(ops) or len(set(ops))!=2:raise AdapterError('RB_INDEPENDENT_EVIDENCE_REQUIRED','Unaccepted or nonindependent calibration')
            hashes=[]
            for op in ops:
                d=self._operation_dir(op);receipt=_load(d/'adapter_receipt.json');intent=_load(d/'adapter_intent.json');req=intent['request'];result=receipt.get('result',{})
                if receipt.get('status')!='completed' or any(req.get(k)!=request.get(k) for k in ('runId','resourceId')) or req.get('procedureId')!=proc or result.get('dataKind')!='real' or result.get('provenance',{}).get('freshAcquisition') is not True:raise AdapterError('RB_RECEIPT_IDENTITY','Previous result not current real acquisition')
                raw=set()
                for a in result.get('artifacts',[]):
                    if a['kind']!='raw':continue
                    f=Path(a['sourcePath']).resolve()
                    if not f.is_relative_to(d) or sha256(f)!=a['sha256']:raise AdapterError('RB_EVIDENCE_HASH','Raw evidence changed')
                    raw.add(a['sha256'])
                if not raw:raise AdapterError('RB_RAW_DATA_REQUIRED','Missing raw calibration')
                hashes.append(raw)
                if op==ops[-1] and any(not same(result['parameters'].get(k),p[k]) for k in CALIBRATION_KEYS):raise AdapterError('RB_STALE_CALIBRATION','Verified physical working point changed')
            if hashes[0]&hashes[1] or seen&hashes[1]:raise AdapterError('RB_REUSED_VALIDATION','Raw calibration data reused')
            seen|=hashes[1];categories.add(category);proof.append({'procedureId':proc,'operations':ops,'rawHashes':[sorted(h) for h in hashes]})
        if categories!={'rabi','readout'}:raise AdapterError('RB_VALIDATION_INCOMPLETE','Need independently accepted current Rabi and readout')
        return {'snapshotHash':_digest(snap),'acceptedEvidence':proof,'parameters':{k:p[k] for k in CALIBRATION_KEYS}}
    def reconcile(self,operation_id):
        path=self._operation_dir(operation_id)/'adapter_receipt.json'
        if path.is_file():
            receipt=_load(path)
            if receipt.get('status') in ('cancelled','failed_after_shutdown') and receipt.get('shutdownConfirmed') is True:
                return {**receipt,'receipt':str(path)}
        return super().reconcile(operation_id)
    def _artifacts(self,d,files):
        artifacts=[]
        for value in dict.fromkeys(files):
            f=Path(value).resolve()
            if not f.is_relative_to(d) or not f.is_file():raise AdapterError('RESULT_PROVENANCE_INVALID','Output outside operation')
            digest=sha256(f);kind='raw' if f.suffix=='.npz' and not f.name.startswith('preflight_') else 'image' if f.suffix=='.png' else 'json' if f.suffix=='.json' else 'text';identity='real-'+digest[:24]
            artifacts.append({'id':identity,'name':f.name,'kind':kind,'url':'/api/artifacts/'+identity,'sha256':digest,'sourcePath':str(f),'localPath':str(f)})
        return artifacts
    def execute(self,request,emit,cancel):
        if self.profile.get('enabled') is not True:raise AdapterError('HARDWARE_DISABLED','ArcLight profile not enabled by controller')
        check=self.preflight()
        if not check['ready']:raise AdapterError('PREFLIGHT_FAILED','ArcLight source checks failed',check)
        op=request.get('operationId');d=self._operation_dir(op);canonical={k:v for k,v in request.items() if k!='outputDir'};request_hash=_digest(canonical)
        if d.exists():
            intent=_load(d/'adapter_intent.json') if (d/'adapter_intent.json').exists() else {}
            if intent.get('requestHash')!=request_hash:raise AdapterError('IDEMPOTENCY_CONFLICT','Operation request differs')
            previous=self.reconcile(op)
            if previous['status']=='completed':return previous['result']
            if previous['status'] in ('cancelled','failed_after_shutdown'):raise AdapterError(previous['status'].upper(),'Operation already stopped; resume must create a new attempt',previous)
            raise AdapterError('NEEDS_RECONCILIATION','Operation has already been submitted',previous)
        plan=self.build_plan(request)
        if cancel():raise AdapterError('CANCELLED_BEFORE_START','Cancelled before hardware worker')
        d.mkdir(parents=True);_dump(d/'adapter_intent.json',{'operationId':op,'requestHash':request_hash,'request':canonical,'createdAt':_utc(),'profileHash':_digest(self.profile)})
        _dump(d/'plan.json',plan)
        env=os.environ.copy();env.pop('PYTHONPATH',None);env.update(QCAL_ARCLIGHT_RUN_DIR=str(d),PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1')
        position=0;stopping=None;started=time.monotonic();delivery_error=None;telemetry_error=None
        def dispatch(e):
            nonlocal delivery_error
            try:emit(e)
            except Exception as exc:delivery_error=str(exc)
        with (d/'worker.log').open('w',encoding='utf8') as log:
            try:
                process=subprocess.Popen([self.profile['python'],'-I','-B','-u',str(self._local(self.profile['runner'])),str(d/'plan.json')],cwd=d,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            except Exception as exc:
                _dump(d/'adapter_receipt.json',{'status':'failed_before_start','operationId':op,'reason':str(exc),'recordedAt':_utc()})
                raise AdapterError('FAILED_BEFORE_START','ArcLight worker could not start') from exc
            _dump(d/'process.json',{'pid':process.pid,'startedAt':_utc()});dispatch({'event':'worker_started','pid':process.pid,'operationId':op})
            def pump():
                nonlocal position,delivery_error,telemetry_error
                # A bad journal must stop delivery, not worker supervision. Keep
                # the original bytes for diagnosis and never retry the bad tail.
                if telemetry_error is not None:return False
                previous=position
                try:position,events=read_event_batch(d/'events.jsonl',position)
                except Exception as exc:
                    telemetry_error=type(exc).__name__+': '+str(exc)
                    delivery_error=telemetry_error
                    return False
                for event in events:dispatch(event)
                return position!=previous
            while process.poll() is None:
                pump()
                try:stop=cancel()
                except Exception as exc:stop=True;delivery_error=str(exc)
                if stopping is None and (stop or delivery_error or time.monotonic()-started>plan['max_seconds']):
                    (d/'STOP_AFTER_POINT').write_text('controller_cancel_or_deadline',encoding='utf8');stopping=time.monotonic()
                if stopping and time.monotonic()-stopping>self.profile['cancelGraceSeconds']:
                    process.kill();process.wait(timeout=10)
                    _dump(d/'adapter_receipt.json',{'status':'unknown','reason':'forced_worker_termination; chassis locks retained','recordedAt':_utc(),
                        **({'telemetryError':telemetry_error} if telemetry_error is not None else {})})
                    raise AdapterError('NEEDS_RECONCILIATION','Worker termination left hardware state unknown')
                time.sleep(.15)
            while pump():pass
        completion=_load(d/'completion.json') if (d/'completion.json').exists() else {}
        if completion.get('state') in ('cancelled','failed_after_shutdown') and completion.get('shutdown_ok') is True:
            receipt={'status':completion['state'],'operationId':op,'requestHash':request_hash,'shutdownConfirmed':True,
                'partialArtifacts':self._artifacts(d,completion.get('files',[])), 'pointsCompleted':completion.get('pointsCompleted',0),
                'reason':completion.get('error'),'recordedAt':_utc(),'completionPath':str(d/'completion.json'),
                **({'telemetryError':telemetry_error} if telemetry_error is not None else {})}
            _dump(d/'adapter_receipt.json',receipt)
            raise AdapterError('CANCELLED_AFTER_SHUTDOWN' if receipt['status']=='cancelled' else 'FAILED_AFTER_SHUTDOWN','ArcLight worker stopped and cleanup completed; partial data retained',receipt)
        if process.returncode!=0 or completion.get('state')!='completed' or completion.get('shutdown_ok') is not True:
            _dump(d/'adapter_receipt.json',{'status':'unknown','reason':completion.get('error','worker outcome unknown'),'completion':completion,'recordedAt':_utc(),
                **({'telemetryError':telemetry_error} if telemetry_error is not None else {})})
            raise AdapterError('NEEDS_RECONCILIATION','ArcLight acquisition or shutdown not fully confirmed',completion)
        result=self._result(d,plan,request,completion)
        _dump(d/'adapter_receipt.json',{'status':'completed','operationId':op,'requestHash':request_hash,'result':result,'recordedAt':_utc(),
            **({'telemetryError':telemetry_error} if telemetry_error is not None else {})})
        return result
    def _result(self,d,plan,request,completion):
        artifacts=self._artifacts(d,completion['files'])
        if not any(a['kind']=='raw' for a in artifacts):raise AdapterError('RAW_DATA_MISSING','No new raw acquisition')
        analysis=completion.get('analysis',{})
        return {'dataKind':'real','sourceRunId':plan['operationId'],'acquiredAt':completion['acquiredAt'],'metrics':analysis.get('metrics',{}),
            'candidateParameters':analysis.get('candidateParameters',{}),'series':analysis.get('series',[]),'analysisQuality':analysis.get('analysis',{}),
            'quality':{'status':'unverified','reasons':['ArcLight native acquisition completed; controller must independently verify frozen quality gates.']},
            'artifacts':artifacts,'parameters':plan['parameters'],'methodLabel':'ArcLight native '+METHODS[request['procedureId']][0],
            'provenance':{'freshAcquisition':True,'operationId':request['operationId'],'parameterVersions':request.get('parameterVersions',{}),
                'runnerSha256':self.profile['runnerSha256'],'sourceManifest':self.profile['sourceManifest'],'profileHash':_digest(self.profile),
                'nativeHalfPolicy':POLICY,'shutdownCommandsCompleted':True,'shutdownReadbackVerified':False,'cleanupReceipt':str(d/'completion.json')}}
    def publish_calibration(self,snapshot):
        # Publication is based only on current accepted independent RB receipts.
        resource=snapshot.get('resourceId');parameters=snapshot.get('parameters',{})
        self._validate_snapshot({'validationSnapshot':snapshot,'runId':snapshot.get('runId'),'resourceId':resource,'parameterVersions':snapshot.get('parameterVersions',{})},parameters)
        rb=next((e for e in snapshot.get('acceptedEvidence',[]) if e.get('procedureId')=='sq_rb' and e.get('status')=='passed'),None)
        if not rb or rb.get('selectionOperationId')==rb.get('verificationOperationId'):raise AdapterError('RB_PUBLICATION_REQUIRED','Accepted independent RB required')
        rb_hash_sets=[]
        for op in (rb.get('selectionOperationId'),rb.get('verificationOperationId')):
            d=self._operation_dir(op);receipt=_load(d/'adapter_receipt.json');req=_load(d/'adapter_intent.json')['request'];result=receipt.get('result',{})
            if receipt.get('status')!='completed' or req.get('procedureId')!='sq_rb' or any(req.get(k)!=snapshot.get(k) for k in ('runId','resourceId')) or result.get('dataKind')!='real' or result.get('provenance',{}).get('freshAcquisition') is not True or result.get('analysisQuality',{}).get('accepted') is not True or any(not same(result['parameters'].get(k),parameters.get(k)) for k in CALIBRATION_KEYS):raise AdapterError('RB_PUBLICATION_INVALID','RB result not accepted at current working point')
            raw=set()
            for artifact in result.get('artifacts',[]):
                if artifact.get('kind')!='raw':continue
                path=Path(artifact['sourcePath']).resolve()
                if not path.is_relative_to(d) or sha256(path)!=artifact['sha256']:raise AdapterError('RB_EVIDENCE_HASH','RB raw evidence changed before publication')
                raw.add(artifact['sha256'])
            if len(raw)<4:raise AdapterError('RB_RAW_DATA_REQUIRED','RB publication needs fresh PRE/reference/interleaved/POST raw data')
            rb_hash_sets.append(raw)
        if rb_hash_sets[0]&rb_hash_sets[1]:raise AdapterError('RB_REUSED_VALIDATION','RB selection and independent verification reused raw data')
        version='arclight-'+_digest(snapshot)
        record={'version':1,'adapter':'arclight','calibrationVersion':version,'status':'VALIDATED_CURRENT_SESSION','snapshot':snapshot,'publishedAt':_utc()};dest=self.root/'data/calibrations'/(version+'.json');dest.parent.mkdir(parents=True,exist_ok=True)
        if not dest.exists():_dump(dest,record)
        elif _load(dest).get('snapshot')!=snapshot:raise AdapterError('CALIBRATION_VERSION_CONFLICT','Published calibration version differs')
        identity='cal-'+sha256(dest)[:24];return {'id':identity,'calibrationVersion':version,'kind':'json','name':dest.name,'sourcePath':str(dest),'localPath':str(dest),'sha256':sha256(dest),'url':'/api/artifacts/'+identity}
