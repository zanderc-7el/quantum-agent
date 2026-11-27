"""Explicit DC time-window migration; never calibrates a shaped rotation.

The narrow receipt authenticator is a versioned copy of frozen V3's immutable
file checks. Only its policy admission is specialized to the rabi_time role;
no f01/pi01 alias is used and the earlier whitelist stays unchanged.
"""
import hashlib
import json
import math
from copy import deepcopy
from pathlib import Path
from adapters.science_runtime.policy import digest,sha
from adapters.science_v3.evidence import result_content,authenticate_dependency as frozen_authenticate

MIGRATION_POLICY='dc_time_workpoint_to_shaped_window_nearest_250ps_v1'
PHYSICAL=('drive_hz','readout_hz','readout_amp','readout_width_s','xy_delay_s','xy_filter_mhz','xy_attenuation_db')

def _finite(value,positive=False):
    if type(value) not in (int,float) or not math.isfinite(value) or (positive and value<=0):
        raise ValueError('Timing source fit/uncertainty must be finite and physically bounded')
    return float(value)

def _same(a,b):
    return type(a) in (int,float) and type(b) in (int,float) and math.isfinite(a) and math.isfinite(b) and math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-22)

def _time_protocol(plan,parameters,resource):
    from adapters.arclight_configurable_runtime.native_single_qubit_specs import rabi_time_spec
    jobs=plan.get('jobs',[])
    if plan.get('procedureId')!='sq_rabi_time' or len(jobs)!=1:raise ValueError('Timing source must be the explicit DC time-Rabi protocol')
    job=jobs[0];spec=job.get('spec',{});sweeps=spec.get('sweeps',[])
    if job.get('kind')!='rabi_time' or job.get('target')!=resource or len(sweeps)!=1:raise ValueError('DC timing protocol scope differs')
    meta=spec.get('meta',{});qubit=meta.get('qubit');axis=sweeps[0].get('values',[])
    if not axis or len(axis)>3000 or any(type(x) not in (int,float) or not math.isfinite(x) or x<0 or abs(x*4e9-round(x*4e9))>1e-7 for x in axis):
        raise ValueError('DC timing sweep is not on the actual native sample grid')
    expected=rabi_time_spec(axis,drive_amplitude=parameters['drive_amp'],shots=meta.get('shots'),qubit=qubit)
    expected['static_config'][f'cfg.Q{qubit}.gate.U_gates.buff']=2e-9
    expected['meta']['pulse_compatibility']='f8_validated_short_half_dc_buff2ns_v1'
    if spec!=expected or job['config'][f'Q{qubit}']['gate']['sRate']['XY']!=4e9:raise ValueError('DC timing source waveform semantics differ')

def _fit_operation(root,op,parameters):
    folder=Path(root)/'data/runs/arclight_operations'/hashlib.sha256(op['operationId'].encode()).hexdigest()
    request=json.loads((folder/'adapter_intent.json').read_text(encoding='utf-8'))['request']
    result=json.loads((folder/'adapter_receipt.json').read_text(encoding='utf-8'))['result']
    plan=json.loads((folder/'plan.json').read_text(encoding='utf-8'));old=result.get('parameters',{})
    if request.get('procedureId')!='sq_rabi_time' or any(old.get(k)!=v for k,v in request.get('parameters',{}).items()) or plan.get('parameters')!=old:
        raise ValueError('Timing request/result/plan parameters differ')
    for key in PHYSICAL:
        if key not in old or key not in parameters or old[key]!=parameters[key]:raise ValueError('Timing physical working point differs: '+key)
    _time_protocol(plan,old,request['resourceId'])
    analysis=result.get('analysisQuality',{})
    if analysis.get('kind')!='rabi_time' or analysis.get('procedureId')!='sq_rabi_time':raise ValueError('Timing analysis protocol differs')
    fits=[]
    for group in analysis.get('groups',[]):
        for repeat in group.get('repeats',[]):
            fit=repeat.get('fit') or {};frequency=_finite(fit.get('value'),True);stderr=_finite(fit.get('standard_error_model'))
            if stderr<0 or stderr/frequency>.1:raise ValueError('Timing fit uncertainty is unresolved')
            fits.append({'frequencyHz':frequency,'frequencyStandardErrorHz':stderr,'continuousDurationS':1/(2*frequency),
                'durationStandardErrorS':stderr/(2*frequency*frequency)})
    if len(fits)<2:raise ValueError('Timing source lacks independent native fit repeats')
    frequency=sum(f['frequencyHz'] for f in fits)/len(fits);duration=1/(2*frequency)
    if not _same(result.get('metrics',{}).get('rabi_frequency_hz'),frequency) or not _same(result.get('candidateParameters',{}).get('drive_length_s'),duration):
        raise ValueError('Timing candidate duration differs from native fit frequency')
    if not 4e-9<=duration<=2e-6:raise ValueError('Timing fit duration outside shaped-window policy')
    return {'stage':op['stage'],'operationId':op['operationId'],'nativeReceiptSha256':sha(folder/'adapter_receipt.json'),
        'fitRepeats':fits,'continuousDurationS':duration,'sourceDriveAmplitude':old['drive_amp'],
        'frequencyHz':frequency,'relativeStandardErrorUpper':max(f['frequencyStandardErrorHz']/f['frequencyHz'] for f in fits),
        'uncertaintyScope':'Native per-repeat model standard errors; not a confidence interval or shaped-rotation calibration'}

def timing_migration(root,request,dependency):
    if dependency.get('procedureId')!='sq_rabi_time' or set(dependency.get('sourceRoles',[]))!={'rabi_time'}:
        raise ValueError('Legacy timing migration permits only the explicit rabi_time role')
    parameters=request.get('parameters',{})
    operations=[_fit_operation(root,op,parameters) for op in dependency['operations']]
    first=next(o for o in operations if o['stage']=='selection');later=next(o for o in operations if o['stage']=='verification')
    if first['sourceDriveAmplitude']!=later['sourceDriveAmplitude']:raise ValueError('Timing source changes drive amplitude between operations')
    duration=first['continuousDurationS'];committed=dependency.get('resolvedOutputs',{}).get('candidateParameters',{}).get('drive_length_s')
    if not _same(committed,round(duration*2e9)/2e9):raise ValueError('Timing source committed duration differs from its old half-duration grid policy')
    target=round(duration*4e9)/4e9;error=target-duration
    if abs(error)>.125e-9+1e-22 or not _same(parameters.get('pulse_length_s'),target):raise ValueError('Shaped time window differs from nearest 0.25 ns timing source')
    migration={'policy':MIGRATION_POLICY,'sourceRecordId':dependency['recordId'],'sourceProcedureId':'sq_rabi_time',
        'scope':'DC working-point and time-window support only','calibratesShapedRotation':False,
        'sourceContinuousDurationS':duration,'sourceCommittedDurationS':committed,'sourceCommittedQuantumS':.5e-9,
        'targetDurationS':target,'sampleQuantumS':.25e-9,'quantizationErrorS':error,'maximumQuantizationErrorS':.125e-9,
        'rounding':'nearest sample, ties to even','fitOperations':operations}
    migration['sha256']=digest(migration)
    return migration

def authenticate_dependency(root,request,dependency,**expected):
    roles=set(dependency.get('sourceRoles',[]))
    from . import beta
    if dependency.get('procedureId')=='sq_drag_half_scan' and beta.empirical(dependency.get('parameters',{})):
        if roles!={'drag'}:raise ValueError('Empirical half scan only supplies the exact drag role')
        return _authenticate_legacy_time(root,request,dependency,_empirical=True,**expected)
    if 'rabi_time' not in roles:
        if 'rabi_amplitude' in roles and dependency.get('procedureId')!='sq_shape_rabi_amplitude':raise ValueError('Rabi amplitude lineage requires exact shaped-amplitude calibration')
        return frozen_authenticate(root,request,dependency,**expected)
    migration=timing_migration(root,request,dependency)
    attestation=_authenticate_legacy_time(root,request,dependency,**expected)
    attestation['timingMigration']=migration
    return attestation

def rabi_lineage(proof,attestations,parameters):
    nodes={node['recordId']:node for node in proof['nodes']};refs={ref['role']:ref['recordId'] for ref in proof['rootReferences']}
    if not {'pi01','rabi_amplitude','rabi_time'}<=set(refs):raise ValueError('Half-DRAG requires explicit amplitude and time Rabi lineage')
    if refs['pi01']!=refs['rabi_amplitude'] or nodes[refs['pi01']].get('procedureId')!='sq_shape_rabi_amplitude':
        raise ValueError('Amplitude Rabi lineage must reuse the same exact shaped pi01 source')
    timing=next((a.get('timingMigration') for a in attestations if a.get('recordId')==refs['rabi_time']),None)
    if not timing or digest({k:v for k,v in timing.items() if k!='sha256'})!=timing.get('sha256') or not _same(timing['targetDurationS'],parameters['pulse_length_s']):
        raise ValueError('Authenticated timing migration is missing or changed')
    value={'schema':'science-v3-rabi-lineage/1','amplitudeRecordId':refs['rabi_amplitude'],'pi01RecordId':refs['pi01'],
        'half01RecordId':refs['half01'],'time':deepcopy(timing),'rotationCalibration':'Exact shaped pi01 and half01 sources, not DC time rotation'}
    value['sha256']=digest(value)
    return value

def inherited_rabi_lineage(root,proof,attestations,parameters):
    nodes={node['recordId']:node for node in proof['nodes']};refs={ref['role']:ref['recordId'] for ref in proof['rootReferences']}
    drag=nodes[refs['drag']];lineages=[]
    for op in drag.get('operations',[]):
        folder=Path(root)/'data/runs/arclight_operations'/hashlib.sha256(op['operationId'].encode()).hexdigest()
        result=json.loads((folder/'adapter_receipt.json').read_text(encoding='utf-8'))['result']
        lineages.append(result.get('provenance',{}).get('scienceHalfDrag',{}).get('rabiLineage'))
    if len(lineages)!=2 or not lineages[0] or lineages[0]!=lineages[1]:raise ValueError('Same-half source lacks consistent amplitude/time lineage in both native receipts')
    lineage=lineages[0]
    if digest({k:v for k,v in lineage.items() if k!='sha256'})!=lineage.get('sha256'):raise ValueError('Inherited Rabi lineage hash changed')
    required={'pi01':lineage['pi01RecordId'],'rabi_amplitude':lineage['amplitudeRecordId'],
              'half01':lineage['half01RecordId'],'rabi_time':lineage['time']['sourceRecordId']}
    for role,record in required.items():
        if not any(edge.get('fromRecordId')==drag['recordId'] and edge['role']==role and edge['toRecordId']==record for edge in proof['edges']):
            raise ValueError('Half-gate Rabi ancestry is missing from the authenticated closure: '+role)
    if refs['pi01']!=required['pi01'] or refs['half01']!=required['half01']:raise ValueError('IRB preparation sources differ from half-gate calibration lineage')
    projection={**proof,'rootReferences':[{'role':role,'recordId':record} for role,record in required.items()]}
    if rabi_lineage(projection,attestations,parameters)!=lineage:raise ValueError('Inherited time migration differs from current independently authenticated source')
    return deepcopy(lineage)

def _authenticate_legacy_time(root,request,dependency,*,expected_route=None,expected_binding=None,expected_locks=None,expected_instrument=None,expected_observed=None,_empirical=False):
    root=Path(root).resolve();ops=dependency.get('operations',[])
    if len(ops)!=2 or {o.get('stage') for o in ops}!={'selection','verification'}:
        raise ValueError('Dependency requires selection and independent-verification operations')
    scope=dependency.get('scope',{})
    if scope.get('resourceId')!=request['resourceId']:raise ValueError('Dependency belongs to another resource')
    cross_workflow=scope.get('workflowId')!=request['runId']
    observed=False
    if expected_observed:
        observed=(expected_observed.get('schema')=='qcal-observed-resource/1' and expected_observed.get('kind')=='observed_resource'
            and expected_observed.get('resourceId')==request['resourceId'] and expected_observed.get('physicalDesignId') is None
            and expected_observed.get('identityClaim')=='measured_resource_not_design_identity'
            and digest({k:v for k,v in expected_observed.items() if k!='sha256'})==expected_observed.get('sha256'))
        if not observed:raise ValueError('Observed-resource binding is malformed or changed')
    if cross_workflow and not observed and not all(scope.get(k) and (expected_binding or {}).get(k)==scope[k] for k in ('chipVersionId','deploymentVersionId')):
        raise ValueError('Cross-workflow seed lacks identical published chip/deployment binding')
    raw_sets=[];receipts=[];checked=[]
    for op in ops:
        folder=root/'data/runs/arclight_operations'/hashlib.sha256(op['operationId'].encode()).hexdigest()
        intent=json.loads((folder/'adapter_intent.json').read_text(encoding='utf-8'))
        receipt=json.loads((folder/'adapter_receipt.json').read_text(encoding='utf-8'))
        result=receipt.get('result',{});actual_request=intent.get('request',{})
        if receipt.get('status')!='completed' or digest(actual_request)!=op.get('adapterRequestSha256'):
            raise ValueError('Dependency operation is incomplete or request hash changed')
        if digest(result_content(result))!=op.get('resultContentSha256'):raise ValueError('Native result differs from controller effect contents')
        # Effect results may add controller quality metadata. Raw adapter result
        # is separately bound by this attestation, not confused with DB hashes.
        if actual_request.get('runId')!=scope.get('workflowId') or actual_request.get('resourceId')!=request.get('resourceId'):
            raise ValueError('Dependency native request scope differs')
        if result.get('dataKind')!='real' or not result.get('acquiredAt') or result.get('provenance',{}).get('freshAcquisition') is not True:
            raise ValueError('Dependency is not an actual fresh native acquisition')
        if result.get('analysisQuality',{}).get('accepted') is not True:
            raise ValueError('Dependency native analysis is not accepted')
        from adapters.science_v3.plans import POLICY
        roles=set(dependency.get('sourceRoles',[]))
        policy=result.get('provenance',{}).get('policy') or result.get('provenance',{}).get('nativeHalfPolicy')
        from . import beta
        if (not _empirical and (policy!='f8_validated_short_half_dc_buff2ns_v1' or roles!={'rabi_time'})) or (_empirical and (policy!=beta.PULSE_POLICY or roles!={'drag'})):
            raise ValueError('Source pulse policy cannot migrate for these dependency roles')
        hashes=set()
        registered={a['sha256'] for a in op.get('rawArtifacts',[])}
        for artifact in result.get('artifacts',[]):
            if artifact.get('kind')!='raw':continue
            path=Path(artifact.get('localPath') or artifact.get('sourcePath','')).resolve()
            if not path.is_relative_to(folder) or sha(path)!=artifact['sha256']:
                raise ValueError('Dependency raw artifact changed or outside operation')
            hashes.add(artifact['sha256'])
            checked.append({'path':str(path),'sha256':artifact['sha256']})
        if not hashes or not registered or not registered<=hashes:raise ValueError('Dependency raw hashes differ from controller evidence')
        completion=json.loads((folder/'completion.json').read_text(encoding='utf-8'))
        plan=json.loads((folder/'plan.json').read_text(encoding='utf-8'))
        if completion.get('state')!='completed' or completion.get('shutdown_ok') is not True or plan.get('operationId')!=op['operationId']:
            raise ValueError('Dependency completion/cleanup identity invalid')
        if cross_workflow:
            if observed:
                origin=(scope.get('workflowId')==expected_observed.get('sourceWorkflowId')
                    and dependency.get('adapterProfileHash')==expected_observed.get('sourceAdapterProfileHash')
                    and actual_request.get('adapterProfileHash')==expected_observed.get('sourceAdapterProfileHash'))
                inherited=plan.get('observedResourceBinding')==expected_observed
                if not origin and not inherited:raise ValueError('Source does not belong to the frozen observed-resource lineage')
            else:
                binding=plan.get('configurationBinding',{})
                if not all(binding.get(k) and binding.get(k)==expected_binding.get(k) for k in ('chipVersionId','deploymentVersionId','chipHash','deploymentHash')):
                    raise ValueError('Source native plan belongs to different published chip/deployment contents')
        if expected_locks is not None and plan.get('hardwareLocks')!=expected_locks:raise ValueError('Source shared chassis ownership differs')
        if expected_instrument is not None and any(plan.get('instrument',{}).get(k)!=expected_instrument.get(k) for k in ('id','address','host','port')):
            raise ValueError('Source instrument endpoint differs')
        if expected_route is not None:
            actual_route=plan.get('routing')
            if actual_route is None:
                from adapters.arclight_configurable_runtime.routing import validate_route
                actual_route=validate_route(plan.get('instrument',{}))
            for key in ('xyPhysical','readoutPhysical','adcPhysical','xyLogical','readoutLogical','adcLogical','adcSampleRateHz'):
                if actual_route.get(key)!=expected_route.get(key):raise ValueError('Prerequisite physical route differs: '+key)
        if _empirical:
            from adapters.f8 import _digest as intent_digest
            from .half_scan import build_half_scan,target_identity
            p=plan.get('parameters',{});candidate=result.get('candidateParameters',{})
            rebuilt,campaign=build_half_scan(p)
            if (plan.get('procedureId')!='sq_drag_half_scan' or not beta.empirical(p) or rebuilt!=p
                    or plan.get('campaign')!=campaign or plan.get('rows')!=campaign['rows']
                    or plan.get('policy')!=beta.HALF_POLICY or plan.get('pulsePolicy')!=beta.PULSE_POLICY
                    or plan.get('sourceHash')!=digest(plan.get('sources'))
                    or plan.get('sourceHash')!=result.get('provenance',{}).get('sourceHash')
                    or plan.get('requestHash')!=digest(actual_request)
                    or receipt.get('operationId')!=op['operationId']
                    or receipt.get('requestHash')!=intent_digest(actual_request)
                    or actual_request.get('procedureId')!='sq_drag_half_scan'
                    or actual_request.get('operationId')!=op['operationId']
                    or intent.get('requestHash')!=intent_digest(actual_request)
                    or completion.get('operationId')!=op['operationId']
                    or set(candidate)!={'drag_beta_s'}
                    or result.get('targetPulseIdentity')!=target_identity(p,candidate['drag_beta_s'])
                    or result.get('parameters')!=p):raise ValueError('Empirical source plan, mode, target or native request changed')
            registered_rows=op.get('registeredArtifacts',[])
            required_names={'plan.json','compiled_plan.json','uploads.jsonl','raw_scan.npz','analysis.json','sequence_manifest.json'}
            artifacts={a['name']:a for a in result.get('artifacts',[])}
            for name in required_names:
                a=artifacts.get(name)
                if not a or not any(all(r.get(k)==a.get(k) for k in ('id','name','kind','sha256')) for r in registered_rows):
                    raise ValueError('Empirical source mandatory metadata is not registered: '+name)
                path=Path(a.get('localPath') or a.get('sourcePath','')).resolve()
                if not path.is_relative_to(folder) or sha(path)!=a['sha256']:raise ValueError('Empirical source metadata changed: '+name)
                checked.append({'path':str(path),'sha256':a['sha256']})
        if policy==POLICY or _empirical:
            compiled=json.loads((folder/'results/compiled_plan.json').read_text(encoding='utf-8'))
            if _empirical and ({r.get('rowId') for r in compiled}!={r['id'] for r in campaign['rows']} or len(compiled)!=len(campaign['rows'])
                    or any(len(r.get('waves',{}))!=2 for r in compiled)):raise ValueError('Empirical compiled row coverage differs')
            uploads=[json.loads(line) for line in (folder/'results/uploads.jsonl').read_text(encoding='utf-8').splitlines() if line]
            expected={(row['rowId'],name):wave for row in compiled for name,wave in row['waves'].items()}
            found=set()
            for upload in uploads:
                key=(upload['rowId'],upload['name'])
                if key not in expected or any(upload.get(k)!=v for k,v in expected[key].items()) or upload.get('uploadCommandsCompleted') is not True:
                    raise ValueError('Dependency upload receipt differs from compiled arrays')
                found.add(key)
            if found!=set(expected):raise ValueError('Dependency has missing waveform uploads')
            for row in compiled:
                path=Path(row['file']).resolve()
                if not path.is_relative_to(folder) or sha(path)!=row['sha256']:raise ValueError('Dependency compiled array archive changed')
                checked.append({'path':str(path),'sha256':row['sha256']})
            checked.extend({'path':str(folder/'results'/name),'sha256':sha(folder/'results'/name)} for name in ('compiled_plan.json','uploads.jsonl','instrument.json'))
        raw_sets.append(hashes)
        checked.extend({'path':str(folder/name),'sha256':sha(folder/name)} for name in ('adapter_intent.json','adapter_receipt.json','completion.json','plan.json'))
        receipts.append({'operationId':op['operationId'],'adapterReceiptSha256':sha(folder/'adapter_receipt.json'),
                         'planSha256':sha(folder/'plan.json'),'completionSha256':sha(folder/'completion.json'),
                         'adapterResultSha256':digest(result),'rawHashes':sorted(hashes)})
    if raw_sets[0]&raw_sets[1]:raise ValueError('Selection and verification reuse raw samples')
    versions=dependency.get('parameterVersions',{});roles=set(dependency.get('sourceRoles',[]))
    relevant={'readout_hz','readout_amp','readout_width_s','xy_delay_s','xy_filter_mhz','xy_attenuation_db'}
    if roles-{'f01'}:relevant|={'drive_hz'}
    if roles&{'readout','pi01','half01'}:relevant|={'drive_amp','drive_length_s'}
    outputs=dependency.get('resolvedOutputs',{}).get('candidateParameters',{})
    for name in (relevant&versions.keys()&request.get('parameterVersions',{}).keys()) if not cross_workflow else ():
        old=versions[name];current=request['parameterVersions'][name]
        own_output=current==old+1 and name in outputs and request.get('parameters',{}).get(name)==outputs[name]
        if current!=old and not own_output:raise ValueError('Dependency consumed physical parameter version changed: '+name)
    return {'status':'valid','role':dependency.get('role'),'recordId':dependency.get('recordId'),'receipts':receipts,
            'dependencySha256':digest(dependency),'consumerSha256':digest({k:v for k,v in request.items() if k!='scienceDependencies'}),
            'evidenceSha256':digest(checked),'checkedFiles':checked,'physicalSourceAuthenticated':True,
            'seedPolicyMigration':dependency.get('adapterProfileHash')!=request.get('adapterProfileHash'),
            'sourceWorkflowId':scope.get('workflowId'),'consumerWorkflowId':request['runId'],'crossWorkflow':cross_workflow,
            'sourceParameterVersions':versions,'consumerParameterVersions':request.get('parameterVersions',{})}
