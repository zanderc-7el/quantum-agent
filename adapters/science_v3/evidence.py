"""Authenticate controller-resolved dependencies against immutable native files."""
import hashlib
import json
from pathlib import Path
from adapters.science_runtime.policy import digest,sha

def check_pulse_dependencies(method,p,rows,dependencies):
    """A trusted source is necessary but its actual pulse must also match."""
    physical=('readout_hz','readout_amp','readout_width_s','xy_delay_s','xy_attenuation_db','xy_filter_mhz')
    for role,dependency in dependencies.items():
        old=dependency.get('parameters',{})
        for key in physical:
            if key not in old or key not in p or old[key]!=p[key]:raise ValueError('Prerequisite physical condition differs: '+role+'/'+key)
    def candidate(role,key):
        d=dependencies[role];values=d.get('resolvedOutputs',{}).get('candidateParameters',{})
        return values.get(key,d.get('parameters',{}).get(key))
    if 'f01' in dependencies and candidate('f01','drive_hz')!=p['drive_hz']:raise ValueError('Current f01 differs from accepted spectroscopy')
    if 'spectrum12' in dependencies and candidate('spectrum12','f12_hz')!=p.get('f12_hz'):raise ValueError('f12 differs from accepted conditional spectrum')
    if 'anharmonicity' in dependencies:
        delta=candidate('anharmonicity','f12_hz')-p['drive_hz']
        if delta!=p.get('anharmonicity_hz'):raise ValueError('DRAG anharmonicity differs from authenticated f12-f01')
    def match(role,label,identity):
        if not isinstance(identity,dict) or digest(identity.get('identity'))!=identity.get('sha256'):raise ValueError('Prerequisite pulse identity missing or changed: '+role)
        native=identity['identity'];pulse=next(g['pulse'] for row in rows for g in row['gates'] if g['pulse']['label']==label)
        from .plans import POLICY
        import math
        expected_context=digest({k:p[k] for k in ('readout_hz','readout_amp','readout_width_s','xy_delay_s')})
        fixed={'policy':POLICY,'rotationRad':math.pi/2 if role=='half01' else math.pi,'bufferS':2e-9,
               'xySampleRateHz':4e9,'readoutContextHash':expected_context,'lambda1':0.,'anharmonicityHz':None}
        for key,value in fixed.items():
            if native.get(key)!=value:raise ValueError('Prerequisite pulse identity differs: '+role+'/'+key)
        keys={'shape':'shape','durationS':'durationNs','sigmaS':'sigmaNs','frequencyHz':'frequencyHz','phaseRad':'phaseRad'}
        for source,target in keys.items():
            value=pulse.get(target,0)*(1e-9 if target.endswith('Ns') else 1)
            if native.get(source)!=value:raise ValueError('Prerequisite pulse differs: '+role+'/'+source)
        amplitude=p['shape_pi_amp'] if label=='target01' else p['drive_amp'] if label=='prep01' else p['probe12_amplitude']
        if role=='half01':amplitude/=2
        if native['amplitude']!=amplitude:raise ValueError('Prerequisite pulse amplitude differs: '+role)
    if 'pi01' in dependencies:
        dependency=dependencies['pi01'];identity=dependency.get('targetPulseIdentity')
        if method.startswith('sq_shelve'):
            if identity:match('pi01','prep01',identity)
            else:
                old=dependency['parameters']
                if p['prep01_shape']!='DC' or old.get('pulse_compatibility')!='f8_validated_short_half_dc_buff2ns_v1':raise ValueError('Legacy seed is only accepted for matching DC preparation')
                if p['drive_amp']!=candidate('pi01','drive_amp') or p['drive_length_s']!=candidate('pi01','drive_length_s'):raise ValueError('Legacy pi01 parameters differ')
        else:match('pi01','target01',identity)
    if 'half01' in dependencies:match('half01','target01',dependencies['half01'].get('targetPulseIdentity'))
    if 'pi12' in dependencies:match('pi12','probe12',dependencies['pi12'].get('pulseIdentities',{}).get('probe12'))

def result_content(result):
    fields=('dataKind','acquiredAt','sourceRunId','parameters','metrics','candidateParameters','series',
            'analysisQuality','provenance','pulseIdentities','targetPulseIdentity','comparison','classifierProtocol')
    content={k:result[k] for k in fields if k in result}
    content['artifacts']=sorted([{k:a[k] for k in ('id','name','kind','sha256')} for a in result.get('artifacts',[])],key=lambda a:a['id'])
    return content

def authenticate_dependency(root,request,dependency,*,expected_route=None,expected_binding=None,expected_locks=None,expected_instrument=None,expected_observed=None):
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
        from .plans import POLICY
        roles=set(dependency.get('sourceRoles',[]))
        policy=result.get('provenance',{}).get('policy') or result.get('provenance',{}).get('nativeHalfPolicy')
        if policy!=POLICY and (policy!='f8_validated_short_half_dc_buff2ns_v1' or not roles or not roles<={'f01','readout','pi01'}):
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
