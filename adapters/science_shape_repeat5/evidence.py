"""Exact repeat5 migration; earlier source authenticators stay frozen."""
import ast
import hashlib
import json
import math
from pathlib import Path
import struct
import zipfile
from adapters.science_runtime.policy import digest,sha
from adapters.science_v3.evidence import result_content,authenticate_dependency as original_authenticate
from .plans import POLICY,PROCEDURE,N,build_repeat5


def authenticate_dependency(root,request,dependency,**expected):
    if dependency.get('procedureId')!=PROCEDURE:return original_authenticate(root,request,dependency,**expected)
    return _authenticate_repeat5(root,request,dependency,**expected)


def _array(archive,name,expected):
    if not name.replace('_','').isalnum():raise ValueError('Invalid native array name')
    info=archive.getinfo(name+'.npy')
    if info.file_size>8_000_000:raise ValueError('Native pulse array exceeds finite budget')
    data=archive.read(info)
    if data[:6]!=b'\x93NUMPY' or data[6:8] not in (b'\x01\x00',b'\x02\x00'):raise ValueError('Unsupported NPY header')
    start=10 if data[6]==1 else 12;size=int.from_bytes(data[8:start],'little')
    if size>4096:raise ValueError('Native array header too large')
    header=ast.literal_eval(data[start:start+size].decode('latin1'));body=data[start+size:]
    if (header.get('descr')!='<f8' or header.get('fortran_order') is not False
            or header.get('shape')!=(expected.get('samples'),) or len(body)!=8*expected['samples']
            or sha_bytes(body)!=expected.get('sha256') or expected.get('dtype')!='<f8'):
        raise ValueError('Actual five-gate baseband array differs from compiled identity')
    values=[v[0] for v in struct.iter_unpack('<d',body)]
    if any(not math.isfinite(v) for v in values):raise ValueError('Nonfinite actual baseband')
    return values


def sha_bytes(value):return hashlib.sha256(value).hexdigest()


def validate_stage_pair(first,second):
    selected=first['candidateParameters']['shape_pi_amp']
    if (second['candidateParameters']!=first['candidateParameters'] or second['targetPulseIdentity']!=first['targetPulseIdentity']
            or second['parameters']!={**first['parameters'],'shape_pi_amp':selected,'verify_shape_pi_amp':selected}):
        raise ValueError('Independent repeat5 operation changed the frozen single-gate candidate or acquisition parameters')

def validate_compiled_row(item,row,folder):
    path=Path(item['file']).resolve()
    if not path.is_relative_to(folder) or sha(path)!=item.get('sha256'):raise ValueError('Compiled five-gate file changed')
    segments=[s for s in item['segments'] if s['channel']=='xy']
    if len(segments)!=N or len(row['gates'])!=N:raise ValueError('Exactly five actual XY segments required')
    single=None
    with zipfile.ZipFile(path) as archive:
        for segment,gate in zip(segments,row['gates']):
            i=_array(archive,segment['basebandI']['array'],segment['basebandI'])
            q=_array(archive,segment['basebandQ']['array'],segment['basebandQ']);pulse=gate['pulse']
            if len(i)!=len(q) or len(i)!=segment['sampleCount'] or segment['sampleRateHz']!=4e9:
                raise ValueError('Five-gate sample count or native clock differs')
            expected_samples=round((pulse['durationNs']+2)*4)
            if (len(i)!=expected_samples or segment['endSample']-segment['startSample']!=expected_samples
                    or segment['shape']!=pulse['shape'] or segment['frequencyHz']!=pulse['frequencyHz']
                    or segment['phaseRad']!=pulse['phaseRad'] or segment['betaSeconds']!=0
                    or not math.isclose(segment['envelopeDurationNs'],pulse['durationNs'],rel_tol=1e-12)
                    or segment['bufferNs']!=2.
                    or (pulse['shape']=='Gaussian' and segment['shapeParameters'].get('sigma')!=pulse['sigmaNs']*1e-9)
                    or any(math.hypot(a,b)>1+1e-12 for a,b in zip(i,q))):raise ValueError('Actual single-pulse physical context differs')
            ai=math.fsum(i)/4e9;aq=math.fsum(q)/4e9
            if not math.isclose(ai,segment['areaISeconds'],rel_tol=1e-12,abs_tol=1e-22) or not math.isclose(aq,segment['areaQSeconds'],rel_tol=1e-12,abs_tol=1e-22):
                raise ValueError('Five-gate area differs from actual native samples')
            signature={k:segment[k] for k in ('sampleCount','sampleRateHz','envelopeDurationNs','bufferNs','shape','shapeParameters','phaseRad','frequencyHz','betaSeconds','areaISeconds','areaQSeconds')}
            signature.update(basebandISha256=segment['basebandI']['sha256'],basebandQSha256=segment['basebandQ']['sha256'])
            if single is None:single=signature
            elif signature!=single:raise ValueError('The five actual pulses are not identical')
    if any(a['endSample']!=b['startSample'] for a,b in zip(segments,segments[1:])):raise ValueError('Five-X sequence has an unexpected gap or overlap')
    evidence={'gateRepeats':N,'singlePulse':single,'singlePulseSha256':digest(single),
        'segmentIds':[s['id'] for s in segments],'startSample':segments[0]['startSample'],
        'endSample':segments[-1]['endSample'],'sampleRateHz':4e9,
        'totalControlDurationS':sum(s['sampleCount']/s['sampleRateHz'] for s in segments),
        'readoutStartSample':next(s['startSample'] for s in item['segments'] if s['channel']=='readout')}
    evidence['sha256']=digest(evidence)
    if item.get('repeat5')!=evidence or not math.isclose(evidence['totalControlDurationS'],row['controlDurationS'],rel_tol=1e-12,abs_tol=1e-22):raise ValueError('Five-pulse count, area or timing evidence differs')


def validate_repeat5_operation(root,folder,plan,completion,result,request,op):
    from .policy import verify_sources
    from .plans import PROFILE_POLICY
    from adapters.f8 import _digest as intent_digest
    from adapters.science_v3.plans import pulse_identity
    p,rows=build_repeat5(plan['parameters']);provenance=result.get('provenance',{})
    if (plan.get('procedureId')!=PROCEDURE or request.get('procedureId')!=PROCEDURE or plan.get('policy')!=POLICY
            or plan.get('adapter')!='science-repeat5' or plan.get('rows')!=rows or p!=plan['parameters']
            or plan.get('requestHash')!=digest(request) or result.get('parameters')!=p
            or plan.get('sourceHash')!=digest(verify_sources(root)) or plan.get('sourceHash')!=provenance.get('sourceHash')):
        raise ValueError('Repeat5 source plan, version or actual request differs')
    intent=json.loads((folder/'adapter_intent.json').read_text('utf8'));receipt=json.loads((folder/'adapter_receipt.json').read_text('utf8'))
    profile=plan.get('profileSnapshot',{})
    from adapters.arclight_configurable_runtime.routing import validate_route,validate_instrument
    if (digest(profile)!=plan.get('profileHash') or intent.get('profileHash')!=intent_digest(profile)
            or provenance.get('profileHash')!=plan['profileHash'] or profile.get('enabled') is not True
            or profile.get('scienceRepeat5Policy')!=PROFILE_POLICY
            or profile.get('runner')!='adapters/science_shape_repeat5/execute_worker.py'
            or profile.get('sourceManifest')!='adapters/science_shape_repeat5/source_manifest.json'
            or profile.get('runnerSha256')!=sha(Path(root)/profile['runner'])
            or validate_route(profile.get('routingByResource',{}).get(request['resourceId']))!=plan.get('routing')
            or validate_instrument(profile.get('instrument',{}))!=plan.get('instrument')
            or profile.get('hardwareLocks')!=plan.get('hardwareLocks')):
        raise ValueError('Repeat5 frozen profile, runner, route or lock identity differs')
    if (intent.get('requestHash')!=intent_digest(request) or receipt.get('requestHash')!=intent_digest(request)
            or receipt.get('operationId')!=op['operationId'] or completion.get('operationId')!=op['operationId']):raise ValueError('Repeat5 native operation envelope differs')
    registered=op.get('registeredArtifacts',[]);artifacts={a['name']:a for a in result['artifacts']}
    for name in ('plan.json','compiled_plan.json','uploads.jsonl','raw_scan.npz','analysis.json','axes.json'):
        item=artifacts.get(name)
        if not item or not any(all(item.get(k)==a.get(k) for k in ('id','name','kind','sha256')) for a in registered):raise ValueError('Repeat5 required file not registered: '+name)
        file=Path(item.get('localPath') or item.get('sourcePath','')).resolve()
        if not file.is_relative_to(folder) or sha(file)!=item['sha256']:raise ValueError('Repeat5 metadata changed: '+name)
    analysis=json.loads((folder/'results/analysis.json').read_text('utf8'))
    if completion.get('analysis')!=analysis or result.get('analysisQuality')!=analysis['analysis'] or result.get('metrics')!=analysis['metrics']:
        raise ValueError('Repeat5 analysis receipt differs')
    calibration=analysis.get('shapeRabiCalibration',{});fit=calibration.get('fit') or {};f=fit.get('value')
    if type(f) not in (int,float) or not math.isfinite(f) or f<=0:raise ValueError('Repeat5 frequency is unresolved')
    candidate=N/(2*f);candidate_value=p.get('verify_shape_pi_amp',candidate)
    if (calibration.get('policy')!=POLICY or calibration.get('gateRepeats')!=N or not p['scan_axis_values'][0]<candidate<p['scan_axis_values'][-1]
            or not 0<candidate<=.99 or not math.isclose(candidate,calibration.get('singleGatePiAmplitude',-1),rel_tol=1e-12)
            or result.get('candidateParameters')!={'shape_pi_amp':candidate_value}
            or provenance.get('shapeRabiCalibration')!=calibration):raise ValueError('Single-gate pi fit or calibration interpretation differs')
    if op['stage']=='verification' and ('verify_shape_pi_amp' not in p or any(result['metrics'].get(k,2)>1 for k in ('candidate_agreement_fraction','candidate_signal_agreement','candidate_capture_agreement'))):
        raise ValueError('Repeat5 fixed independent candidate is missing or inconsistent')
    if op['stage']=='selection' and 'verify_shape_pi_amp' in p:raise ValueError('Selection cannot be an already fixed verification candidate')
    pulse=dict(rows[0]['gates'][0]['pulse'],amplitude=candidate_value)
    identity=pulse_identity(pulse,{k:p[k] for k in ('readout_hz','readout_amp','readout_width_s','xy_delay_s')})
    if result.get('targetPulseIdentity')!={'identity':identity,'sha256':digest(identity)}:raise ValueError('Repeat5 single-gate target identity differs')
    compiled=json.loads((folder/'results/compiled_plan.json').read_text('utf8'))
    if len(compiled)!=len(rows) or {r['rowId'] for r in compiled}!={r['id'] for r in rows}:raise ValueError('Repeat5 compiled row coverage differs')
    by_id={r['id']:r for r in rows}
    for item in compiled:validate_compiled_row(item,by_id[item['rowId']],folder)
    evidence={'gateRepeats':N,'rows':[{'rowId':r['rowId'],**r['repeat5']} for r in compiled]}
    if completion.get('repeat5Evidence')!=evidence or provenance.get('compiledSequenceEvidence')!=evidence:raise ValueError('Five-gate provenance differs from native archives')


def _authenticate_repeat5(root,request,dependency,*,expected_route=None,expected_binding=None,expected_locks=None,expected_instrument=None,expected_observed=None):
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
    raw_sets=[];receipts=[];checked=[];stage_results={}
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
        from .plans import POLICY
        roles=set(dependency.get('sourceRoles',[]))
        policy=result.get('provenance',{}).get('policy') or result.get('provenance',{}).get('nativeHalfPolicy')
        if policy!=POLICY or not roles or not roles<={'pi01','rabi_amplitude'}:
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
        validate_repeat5_operation(root,folder,plan,completion,result,actual_request,op)
        stage_results[op['stage']]=result
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
        if policy==POLICY:
            compiled=json.loads((folder/'results/compiled_plan.json').read_text(encoding='utf-8'))
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
    validate_stage_pair(stage_results['selection'],stage_results['verification'])
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
