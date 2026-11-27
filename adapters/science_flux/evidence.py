"""Native receipt authentication and exact selected measured-point provenance."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from adapters.science_runtime.policy import digest,sha
from .contracts import POLICY,validate_held_receipt,is_commanded,policy_for

def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def consumer(request):return {k:v for k,v in request.items() if k!='scienceDependencies'}

def _file(folder,value,expected,checked):
    path=Path(value).resolve()
    if not path.is_relative_to(folder) or not path.is_file() or sha(path)!=expected:raise ValueError('Native evidence file path/hash differs')
    checked.append({'path':str(path),'sha256':expected});return path

def authenticate_flux_dependency(root,request,dependency,*,route,instrument,binding,observed,bias_route,expected_locks):
    from backend.science_dependencies import result_content
    from adapters.science_flux_adapter import verify_sources,shared_locks
    from adapters.f8 import _digest as transport_digest
    root=Path(root).resolve();ops=dependency.get('operations',[]);scope=dependency.get('scope',{})
    if dependency.get('procedureId')!='ro_flux_scan' or set(dependency.get('sourceRoles',[]))!={'flux_scan'}:raise ValueError('Only measured flux-scan sources are eligible for a working point')
    if len(ops)!=2 or {o.get('stage') for o in ops}!={'selection','verification'}:raise ValueError('Flux point requires independent selection and verification')
    if scope.get('resourceId')!=request['resourceId']:raise ValueError('Flux source resource differs')
    checked=[];raw_sets=[];operation_points={}
    for operation in ops:
        folder=(root/'data/runs/arclight_operations'/hashlib.sha256(operation['operationId'].encode()).hexdigest()).resolve()
        intent=read(folder/'adapter_intent.json');receipt=read(folder/'adapter_receipt.json');plan=read(folder/'plan.json');completion=read(folder/'completion.json')
        actual=intent.get('request',{});result=receipt.get('result',{});science=result.get('provenance',{}).get('scienceFlux',{})
        if digest(actual)!=operation.get('adapterRequestSha256') or digest(result_content(result))!=operation.get('resultContentSha256'):raise ValueError('Flux native request/result differs from controller effect')
        if receipt.get('status')!='completed' or completion.get('state')!='completed' or completion.get('testOnly') is not False or completion.get('shutdown_ok') is not True:raise ValueError('Flux source is incomplete, fixture, or unrestored')
        if any(actual.get(k)!=v for k,v in {'operationId':operation['operationId'],'runId':scope['workflowId'],'resourceId':request['resourceId'],'attemptId':dependency['attemptId']}.items()):raise ValueError('Flux source operation scope differs')
        commanded=is_commanded(bias_route)
        if plan.get('policy')!=policy_for(bias_route) or plan.get('adapter')!='science-flux' or plan.get('requestHash')!=digest(actual):raise ValueError('Flux native plan policy/request differs')
        if any(doc.get('policy',None if commanded else plan['policy'])!=plan['policy'] for doc in (completion,science)):raise ValueError('Flux completion/result policy differs')
        if any(doc.get('operationId')!=operation['operationId'] or doc.get('resourceId')!=request['resourceId'] for doc in (plan,completion)):raise ValueError('Flux plan/completion operation identity differs')
        if intent.get('requestHash')!=transport_digest(actual) or plan.get('transportRequestHash')!=transport_digest(actual):raise ValueError('Flux transport request digest differs')
        profile=plan.get('profileSnapshot',{})
        if profile.get('enabled') is not True or digest(profile)!=plan.get('profileHash') or transport_digest(profile)!=intent.get('profileHash'):raise ValueError('Flux frozen source profile differs')
        if plan.get('hardwareLocks')!=shared_locks(root,profile) or not set(expected_locks)<=set(plan['hardwareLocks']):raise ValueError('Flux source shared chassis/module/meter locks differ')
        sources=verify_sources(root)
        if plan.get('sources')!=sources or plan.get('sourceHash')!=digest(sources):raise ValueError('Flux source-code policy closure differs')
        if plan.get('biasRoute')!=bias_route or plan.get('routing')!=route or any(plan.get('instrument',{}).get(k)!=instrument.get(k) for k in ('id','host','port','address')):raise ValueError('Flux source physical route differs')
        if observed:
            if plan.get('observedResourceBinding')!=observed:raise ValueError('Flux source observed-resource lineage differs')
        elif not binding or any(not binding.get(k) or plan.get('configurationBinding',{}).get(k)!=binding[k] for k in ('chipVersionId','deploymentVersionId','chipHash','deploymentHash')):raise ValueError('Flux source published binding differs')
        if result.get('dataKind')!='real' or result.get('analysisQuality',{}).get('accepted') is not True or result.get('provenance',{}).get('freshAcquisition') is not True:raise ValueError('Flux source is not accepted fresh physical acquisition')
        if science.get('points')!=completion.get('points') or science.get('restoration')!=completion.get('restoration') or science.get('initial')!=completion.get('initial'):raise ValueError('Flux result lost native bias receipt identity')
        journal=read(folder/'results/bias_journal.json')
        expected_end='returned_to_declared_setting' if commanded else 'restored'
        if journal.get('state')!=expected_end or journal.get('restoration')!=completion.get('restoration') or journal.get('initial')!=completion.get('initial'):raise ValueError('Flux restoration journal differs')
        end_flag='commandEndVerified' if commanded else 'verified'
        if completion['restoration'].get(end_flag) is not True or completion.get('rfShutdown',{}).get('verified') is not True:raise ValueError('Flux shutdown/end action is unconfirmed')
        if completion.get('providerClosed') is not True or completion.get('transportClose',{}).get('shutdown_ok') is not True:raise ValueError('Flux provider/transport closure is unconfirmed')
        initial=completion['initial'];restored=completion['restoration']
        command_events=None
        if commanded:
            from .command_contracts import validate_command_journal,validate_authorization
            identity={'operationId':plan['operationId'],'resourceId':plan['resourceId']}
            authorization=validate_authorization(actual.get('biasCommandAuthorization'),bias_route,actual)
            if plan.get('biasCommandAuthorization')!=authorization or science.get('commandAuthorization')!=authorization:raise ValueError('Source end/start operation authorization differs')
            from .command_lease import lease_context,validate_prior_entry
            if authorization['stage']!=operation['stage'] or plan.get('commandLease')!=lease_context(actual,operation['stage']):raise ValueError('Source command stage/lease differs')
            if science.get('commandLease')!=plan['commandLease'] or science.get('commandContinuation')!=completion.get('commandContinuation') or science.get('completionSha256')!=sha(folder/'completion.json'):raise ValueError('Source command continuity evidence differs')
            if operation['stage']=='verification':
                selected=next(o['operationId'] for o in ops if o['stage']=='selection')
                if authorization['entryEvidence']['operationId']!=selected:raise ValueError('Verification did not enter from this selection end')
                validate_prior_entry(folder.parent,plan)
            command_events=validate_command_journal(journal,bias_route,identity,authorization)
        else:
            validate_held_receipt(initial,bias_route)
            validate_held_receipt(restored,bias_route,setting=initial['setting']['valueV'],measured=initial['measurement']['valueV'])
            a,b=initial['measurement'],restored['measurement']
            if abs(a['valueV']-b['valueV'])+a['uncertaintyV']+b['uncertaintyV']+bias_route['offsetToOutput']['uncertaintyV']>bias_route['toleranceV']:raise ValueError('Restoration measured outputs disagree')
        def check_bias(value,setting):
            validate_held_receipt(value,bias_route,setting=setting)
            if command_events is not None and command_events.get(value['command']['id'])!=value['command']:raise ValueError('Point command does not belong to source operation journal')
        hashes=set();registered={tuple(a.get(k) for k in ('id','name','kind','sha256')) for a in operation.get('registeredArtifacts',[])};registered_paths=set()
        for artifact in result.get('artifacts',[]):
            if tuple(artifact.get(k) for k in ('id','name','kind','sha256')) not in registered:raise ValueError('Native artifact lacks controller registration identity')
            path=_file(folder,artifact.get('localPath') or artifact.get('sourcePath',''),artifact['sha256'],checked)
            registered_paths.add(path)
            if artifact.get('kind')=='raw':hashes.add(artifact['sha256'])
        if not hashes or not {r['sha256'] for r in operation.get('rawArtifacts',[])}<=hashes:raise ValueError('Flux raw set is missing or changed')
        raw_sets.append(hashes)
        compiled=read(folder/'results/compiled_plan.json');uploads=[json.loads(line) for line in (folder/'results/uploads.jsonl').read_text(encoding='utf-8').splitlines() if line]
        required={folder/name for name in ('adapter_intent.json','plan.json','completion.json','results/bias_journal.json','results/compiled_plan.json','results/uploads.jsonl','results/instrument.json')}
        required.update(Path(item['file']).resolve() for item in compiled)
        required.update(folder/f'results/bias_row_{row_id:04d}.json' for point in completion['points'] for row_id in point['rowIds'])
        if not required<=registered_paths:raise ValueError('Native metadata/row/compiled evidence is not registered and effect-bound')
        expected={(item['rowId'],key):value for item in compiled for key,value in item['waves'].items()};found=set()
        for upload in uploads:
            key=(upload['rowId'],upload['name'])
            if key not in expected or any(upload.get(k)!=v for k,v in expected[key].items()) or upload.get('uploadCommandsCompleted') is not True:raise ValueError('Flux compiled/upload identity differs')
            found.add(key)
        if found!=set(expected):raise ValueError('Flux waveform upload receipt is incomplete')
        for item in compiled:_file(folder,item['file'],item['sha256'],checked)
        for point in completion['points']:
            if digest({k:v for k,v in point.items() if k!='cutSha256'})!=point.get('cutSha256'):raise ValueError('Flux cut identity changed')
            _file(folder,point['rawPath'],point['rawSha256'],checked)
            check_bias(point['biasReceipt'],point['requestedSettingV'])
            for row_id in point['rowIds']:
                row=read(folder/f'results/bias_row_{row_id:04d}.json')
                if row.get('pointId')!=point['pointId'] or row.get('rowId')!=row_id:raise ValueError('Missing row/bias identity')
                for key in ('before','after'):check_bias(row[key],point['requestedSettingV'])
        operation_points[operation['stage']]={'operationId':operation['operationId'],'points':completion['points']}
        for name in ('adapter_intent.json','adapter_receipt.json','plan.json','completion.json','results/bias_journal.json','results/compiled_plan.json','results/uploads.jsonl'):
            checked.append({'path':str(folder/name),'sha256':sha(folder/name)})
    if raw_sets[0]&raw_sets[1]:raise ValueError('Independent flux captures reuse raw bytes')
    return {'status':'valid','dependencySha256':digest(dependency),'consumerSha256':digest(consumer(request)),
        'evidenceSha256':digest(checked),'checkedFiles':checked,'physicalSourceAuthenticated':True,'operationPoints':operation_points}

POINT_FIELDS={'schema','workingPointId','recordId','pointId','resourceId','purpose','requestedSettingV','measuredOutputV',
    'measurementUncertaintyV','routeSha256','usableReadoutBandHz','originalCut','sourceOperations','sha256'}

def validate_point(source,dependency,attestation,bias_route):
    commanded=is_commanded(bias_route)
    schema='qcal-flux-point-evidence/2' if commanded else 'qcal-flux-point-evidence/1'
    if not isinstance(source,dict) or set(source)!=POINT_FIELDS or source.get('schema')!=schema:raise ValueError('Complete server-resolved point schema required')
    if source.get('purpose') not in ('idle','work') or not isinstance(source.get('workingPointId'),str) or not 1<=len(source['workingPointId'])<=256:raise ValueError('Working point purpose/identity is missing')
    if digest({k:v for k,v in source.items() if k!='sha256'})!=source['sha256']:raise ValueError('Point evidence hash differs')
    if source['recordId']!=dependency['recordId'] or source['resourceId']!=bias_route['resourceId'] or source['routeSha256']!=digest(bias_route):raise ValueError('Point source/resource/route differs')
    expected_ops=[];verified=None;selected=[]
    for stage in ('selection','verification'):
        op=attestation['operationPoints'][stage]
        matches=[p for p in op['points'] if p['pointId']==source['pointId'] and p['phase']=='cavity']
        if len(matches)!=1 or matches[0]['analysis'].get('accepted') is not True:raise ValueError('Selected point is unresolved in an independent source operation')
        point=matches[0]
        selected.append(point)
        if point['requestedSettingV']!=source['requestedSettingV']:raise ValueError('Selected offset differs from native source')
        expected_ops.append({'stage':stage,'operationId':op['operationId'],'cutSha256':point['cutSha256'],
            'pointReceiptSha256':digest(point['biasReceipt']),'rawSha256':point['rawSha256']})
        if stage=='verification':verified=point
    first,second=selected
    if first['rawSha256']==second['rawSha256']:raise ValueError('Independent source point reuses raw samples')
    if commanded:
        for point in selected:validate_held_receipt(point['biasReceipt'],bias_route,setting=source['requestedSettingV'])
    else:
        a,b=(p['biasReceipt']['measurement'] for p in selected)
        if abs(a['valueV']-b['valueV'])+a['uncertaintyV']+b['uncertaintyV']>bias_route['toleranceV']:raise ValueError('Independent source point measured biases disagree')
    if first['frequenciesHz']!=second['frequenciesHz']:raise ValueError('Independent point cavity axes differ')
    tolerance=min(p['analysis']['frequencyToleranceHz'] for p in selected)
    if abs(first['readoutFrequencyHz']-second['readoutFrequencyHz'])>tolerance:raise ValueError('Independent source point cavity frequencies disagree')
    if source['sourceOperations']!=expected_ops:raise ValueError('Point operation/cut/raw identities differ')
    m=verified['biasReceipt']['measurement']
    if commanded:
        if source['measuredOutputV'] is not None or source['measurementUncertaintyV'] is not None:raise ValueError('Command point cannot claim measured voltage')
    elif source['measuredOutputV']!=m['valueV'] or source['measurementUncertaintyV']!=m['uncertaintyV']:raise ValueError('Point measured output differs from native receipt')
    expected={'frequenciesHz':verified['frequenciesHz'],'responseMagnitude':verified['responseMagnitude'],'readoutFrequencyHz':verified['readoutFrequencyHz']}
    if source['originalCut']!=expected or source['usableReadoutBandHz']!=[verified['frequenciesHz'][0],verified['frequenciesHz'][-1]]:raise ValueError('Point source cavity spectrum/band differs')
    return deepcopy(source)
