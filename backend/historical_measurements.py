"""Bounded, read-only historical measurements for an active model decision.

References never enter the current-attempt evidence allowlist, calibration ledger,
or execution authorization. The first contract supports the already audited
standard ArcLight platform-analysis receipts, not unrelated scientific schemas.
"""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path

from .common import APIError, checked_path, digest, dumps, file_hash, number, now
from .chat_acquisition_context import read_chat_snapshot, beijing_time
from .capture_diagnostics import _registered_files
from .prior_failure_context import CHECKED_JSON_NAMES, IMPLEMENTATION, load_audited_bindings, _validate_effect
from .quality import evaluate

SCHEMA='qcal-historical-evidence-search/1'
RESULT_SCHEMA='qcal-historical-measurements/1'
MAX_CASES=3
MAX_CANDIDATES=32
MAX_BYTES=15000
MAX_HASH_BYTES=512*1024*1024
MAX_DOCUMENT=16*1024*1024
SUPPORTED=frozenset(('ro_resonator_survey','ro_power_shift','sq_spectroscopy','sq_rabi_amplitude',
    'sq_rabi_time','sq_rabi_frequency','sq_allxy','ro_iq01','ro_optimize','ro_optimize_frequency',
    'ro_s21_ge','sq_t1','sq_ramsey','sq_echo','sq_cpmg'))
NOTICE=('历史独立首复实测参考，非本轮校准、当前证据或下发授权。不同读出、频率、脉宽与版本须分别核对；'
        'sourceReferences/caseRef不得填入当前decision.evidenceIds。检索不会更改参数、门槛或任务。'
        '固定机台路由相同不等于已证实芯片设计身份或片端工况不变；无合格记录不表示实验从未做过。')

def require(ok, code):
    if not ok:raise ValueError(code)

def capability(workflow,node):
    if workflow.get('mode')!='real' or node.get('procedureId') not in SUPPORTED:return None
    return {'schema':SCHEMA,'runId':workflow['id'],'nodeId':node['id'],'revision':workflow['revision'],
        'procedureId':node['procedureId'],'resourceId':node['resourceId']}

def _object(value):
    require(isinstance(value,str) and len(value.encode('utf8'))<=MAX_DOCUMENT,'document_budget')
    out=json.loads(value);require(isinstance(out,dict),'invalid_document');return out

def _document(path,budget):
    path=checked_path(path);size=path.stat().st_size
    require(0<size<=MAX_DOCUMENT and size<=budget[0],'archive_read_budget');budget[0]-=size
    raw=path.read_bytes();value=json.loads(raw);require(isinstance(value,dict),'invalid_archive_document')
    return value,hashlib.sha256(raw).hexdigest()

def _comparison_parameters(node):
    result=node.get('result') or {}
    return {k:v for k,v in result.get('parameters',{}).items() if number(v) or isinstance(v,str) and len(v)<=120}

def _axis(plan):
    rows=[]
    for job in plan.get('jobs',[])[:4]:
        for axis in job.get('spec',{}).get('sweeps',[])[:8]:
            values=axis.get('values')
            if not isinstance(values,list) or not values or not all(number(v) for v in values):continue
            rows.append({'binding':axis.get('bindings'),'count':len(values),'first':values[0],'last':values[-1],
                'minimum':min(values),'maximum':max(values),'valuesSha256':digest(values),
                'values':values if len(values)<=16 else None,'valuesComplete':len(values)<=16,
                'stepMinimum':min(b-a for a,b in zip(values,values[1:])) if len(values)>1 else None,
                'stepMaximum':max(b-a for a,b in zip(values,values[1:])) if len(values)>1 else None})
    return rows

def _physical_identity(source,node,current,current_node,source_plan,current_binding):
    # Only exact frozen configuration bindings may support a design identity.
    # Standard fixed profiles have no published chip identity; retain that limit.
    a,b=source.get('configurationBinding'),current.get('configurationBinding')
    published=bool(a and b and source.get('chipVersionId') and source.get('deploymentVersionId')
        and source.get('chipVersionId')==current.get('chipVersionId')
        and source.get('deploymentVersionId')==current.get('deploymentVersionId') and digest(a)==digest(b))
    same_route=source_plan.get('instrument')==current_binding['profile'].get('instrument')
    return {'classification':'same_published_binding' if published and same_route else
            ('same_frozen_route_reference' if same_route else 'different_route_reference'),
        'sameResourceLabel':node['resourceId']==current_node['resourceId'],
        'sameFrozenInstrumentRoute':same_route,'samePublishedBinding':published,
        'physicalDesignIdentityConfirmed':False,'routeSha256':digest(source_plan.get('instrument')),
        'currentRouteSha256':digest(current_binding['profile'].get('instrument')),
        'meaning':'配置路由与资源标签的比较；不宣称片端偏置、温度、接线现状未变化。'}

def _source_files(root,binding,checked):
    # Validate the complete frozen code closure once per profile in this request.
    for item in binding['sourceFiles']:
        key=(item['path'],item['sha256'])
        if key not in checked:
            path=checked_path(root/item['path'],parent=root)
            require(file_hash(path)==item['sha256'],'source_file_changed');checked.add(key)

def _operation(db,root,workflow,node,attempt,binding,operation,expected,budget):
    row=db.execute('SELECT * FROM platform_effects WHERE operation_id=? AND workflow_id=? AND attempt_id=?',
        (operation,workflow['id'],attempt['id'])).fetchone()
    require(row and row['state']=='completed' and row['result'],'incomplete_effect')
    effect=dict(row);effect['request']=_object(row['request']);effect['result']=_object(row['result'])
    inventory=effect['result'].get('artifacts') or []
    require(len(inventory)<=4096,'artifact_inventory_limit')
    needed=[a for a in inventory if a.get('kind')=='raw' or a.get('name') in CHECKED_JSON_NAMES|{'axes.json'}]
    directory=checked_path(root/'data/runs/arclight_operations'/hashlib.sha256(operation.encode()).hexdigest(),parent=root)
    size=sum(checked_path(a['sourcePath'],parent=directory).stat().st_size for a in needed)
    require(size<=budget[0],'archive_read_budget');budget[0]-=size
    files=_registered_files(db,root,operation,effect['result'])
    raw=_validate_effect(effect,[f for f in files if f['kind']=='raw' or f['name'] in CHECKED_JSON_NAMES],
        workflow,attempt,binding,operation,expected)
    request=effect['request'];canonical={k:v for k,v in request.items() if k!='outputDir'}
    native_digest=lambda v:hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    intent,ih=_document(directory/'adapter_intent.json',budget)
    receipt,rh=_document(directory/'adapter_receipt.json',budget)
    completion,ch=_document(directory/'completion.json',budget)
    require(intent.get('operationId')==operation and intent.get('request')==canonical
        and intent.get('requestHash')==native_digest(canonical)
        and intent.get('profileHash') in binding['executionProfileHashes'],'intent_binding_mismatch')
    require(receipt.get('status')=='completed' and receipt.get('operationId')==operation
        and receipt.get('requestHash')==native_digest(canonical),'receipt_binding_mismatch')
    keys=('parameters','metrics','series','analysisQuality','candidateParameters','sourceRunId','acquiredAt','provenance')
    require(all(receipt.get('result',{}).get(k)==effect['result'].get(k) for k in keys),'receipt_content_mismatch')
    require(completion.get('state')=='completed' and completion.get('shutdown_ok') is True
        and completion.get('acquiredAt')==effect['result'].get('acquiredAt'),'completion_invalid')
    analysis=completion.get('analysis') or {}
    require(isinstance(analysis,dict) and all(analysis.get(a)==effect['result'].get(b) for a,b in
        (('metrics','metrics'),('series','series'),('candidateParameters','candidateParameters'),('analysis','analysisQuality'))),
        'completion_analysis_mismatch')
    completed_paths=completion.get('files') or []
    require(isinstance(completed_paths,list) and len(completed_paths)<=4096,'completion_inventory_limit')
    completed_paths={checked_path(p,parent=directory) for p in completed_paths}
    require(all(checked_path(a['sourcePath'],parent=directory) in completed_paths for a in needed if a['kind']=='raw'),
        'completion_raw_missing')
    plan=next(f['document'] for f in files if f['name']=='plan.json')
    require(plan.get('instrument')==binding['profile']['instrument'] and
        plan.get('hardwareLocks')==binding['profile']['hardwareLocks'],'route_binding_mismatch')
    require(evaluate(effect['result'],attempt['procedureId'],'real',workflow['gates'],independent=True,
        rule_version=workflow['gatesVersion'])['quality']['status']=='passed'
        and effect['result'].get('analysisQuality',{}).get('accepted') is True,'native_or_numeric_quality_failed')
    return effect['result'],raw,plan,files,{'intentSha256':ih,'adapterReceiptSha256':rh,'completionSha256':ch}

def _case(db,root,current,current_node,current_binding,source,attempt,binding,budget):
    nodes=[n for n in source['nodes'] if n['id']==attempt.get('nodeId')]
    require(len(nodes)==1,'source_node_missing');node=nodes[0];result=attempt.get('result') or {}
    require(node.get('resourceId')==current_node['resourceId'],'resource_mismatch')
    require(attempt.get('procedureId')==current_node['procedureId']==node.get('procedureId'),'method_mismatch')
    require(attempt.get('mode')=='real' and source.get('mode')=='real' and attempt.get('state')=='verified'
        and node.get('engineState')=='verified' and node.get('latestAttemptId')==attempt['id']
        and attempt.get('endedAt') and node.get('result')==result,'source_not_currently_accepted')
    require(not any(result.get(k) for k in ('partial','stale','verificationPending')),'source_stale_or_partial')
    require(result.get('quality',{}).get('status')=='passed'
        and result['quality'].get('criteria')==source['gates'][node['procedureId']]
        and result['quality'].get('ruleVersion')==source['gatesVersion'],'source_quality_changed')
    require(not attempt.get('executionBinding'),'special_execution_schema_not_supported')
    operations=[attempt.get('operationId'),attempt.get('verificationOperationId')]
    require(operations==[attempt['id']+'/measure',attempt['id']+'/verify'],'independent_operations_missing')
    require(db.execute('SELECT count(*) FROM platform_effects WHERE attempt_id=?',(attempt['id'],)).fetchone()[0]==2,'unexpected_effects')
    saved=result.get('selectionResult');require(isinstance(saved,dict),'selection_missing')
    summaries=[];raw_sets=[];plans=[]
    for stage,op,expected in zip(('selection','verification'),operations,(saved,result)):
        native,raw,plan,files,hashes=_operation(db,root,source,node,attempt,binding,op,expected,budget)
        raw_sets.append(raw);plans.append(plan)
        summaries.append({'stage':stage,'operationRef':op,'acquiredAt':native['acquiredAt'],
            'acquiredAtBeijing':beijing_time(native['acquiredAt']),
            'parameters':deepcopy(native['parameters']),'measuredCandidates':native['candidateParameters'],
            'metrics':{k:v for k,v in native.get('metrics',{}).items() if number(v)},'nativeAccepted':True,
            'axis':_axis(plan),'totalRows':plan.get('totalRows'),'shots':plan.get('estimated_total_shots'),
            'sourceReferences':[{k:f[k] for k in ('artifactRef','name','sha256')} for f in files
                if f['name'] in ('raw_scan.npz','plan.json','platform_analysis.json')],**hashes})
    require(raw_sets[0].isdisjoint(raw_sets[1]),'independent_raw_reused')
    require(datetime.fromisoformat(summaries[1]['acquiredAt'].replace('Z','+00:00'))>
        datetime.fromisoformat(summaries[0]['acquiredAt'].replace('Z','+00:00')),'independent_time_order_invalid')
    # Use the source adapter's existing normalization, never the consumer's policy.
    from adapters.arclight import ArcLightAdapter
    adapter=ArcLightAdapter(root,binding['profile'])
    require(adapter.normalize_candidate(node['procedureId'],summaries[0]['measuredCandidates'])==result.get('candidateParameters'),
        'committed_candidate_mismatch')
    require(result.get('verificationCandidateParameters')==summaries[1]['measuredCandidates'],'verification_candidate_mismatch')
    current_parameters=_comparison_parameters(current_node);old=summaries[0]['parameters']
    differences={k:{'historical':old.get(k),'currentAcquisition':current_parameters.get(k)}
        for k in sorted(set(old)|set(current_parameters)) if old.get(k)!=current_parameters.get(k)}
    # Lossless compact projection: the verification parameters are the complete
    # selection parameters plus the explicit differing fields, not a new fit.
    verify_parameters=summaries[1].pop('parameters')
    summaries[1]['parametersBasis']='selection.parameters_with_these_replacements'
    summaries[1]['parameterReplacements']={k:v for k,v in verify_parameters.items() if k not in old or old[k]!=v}
    summaries[1]['parameterKeysRemoved']=[k for k in old if k not in verify_parameters]
    summaries[1]['parametersSha256']=digest(verify_parameters)
    if summaries[1]['axis']==summaries[0]['axis']:
        summaries[1]['axis']={'sameAsSelection':True,'sha256':digest(summaries[0]['axis'])}
    return {'caseRef':'historical-'+digest([source['id'],attempt['id']])[:24],
        'sourceWorkflow':source['id'],'sourceAttempt':attempt['id'],'procedure':node['procedureId'],'resource':node['resourceId'],
        'status':'historical_independently_accepted_reference','acceptedAt':node.get('humanAcceptedAt',attempt['endedAt']),
        'physicalIdentity':_physical_identity(source,node,current,current_node,plans[0],current_binding),
        'comparisonBasis':{'meaning':'current_latest_acquisition_not_next_plan','currentAttempt':current_node.get('latestAttemptId')},
        'parameterDifferences':differences,'submittedCandidates':result.get('candidateParameters'),
        'versions':{'sourceAdapter':source['adapterId'],'sourceProfileHash':source['adapterProfileHash'],
            'consumerAdapter':current['adapterId'],'consumerProfileHash':current['adapterProfileHash'],
            'sourceManifestSha256':binding['sourceManifestSha256'],'analysisSourceSha256':binding['manifest']['platform_analysis.py'],
            'consumerAnalysisSourceSha256':current_binding['manifest']['platform_analysis.py'],
            'nativeCoreSourceSha256':digest({k:v for k,v in binding['manifest'].items() if k.startswith('work/server/core/')}),
            'sourceParameterVersionsSha256':digest(attempt.get('parameterVersions'))},'operations':summaries,
        'currentCalibration':False,'executionAuthorized':False}

def search(root,db_path,workflow_id,node_id,revision,limit=2):
    if type(revision) is not int or revision<0 or type(limit) is not int or not 1<=limit<=MAX_CASES:
        raise APIError('BAD_HISTORY_QUERY','历史查询版本或条数无效')
    root=checked_path(root)
    with read_chat_snapshot(db_path) as db:
        row=db.execute('SELECT payload FROM platform_workflows WHERE id=?',(workflow_id,)).fetchone()
        if not row:raise APIError('NOT_FOUND','任务不存在',404)
        current=_object(row[0]);nodes=[n for n in current['nodes'] if n['id']==node_id]
        if len(nodes)!=1:raise APIError('NOT_FOUND','节点不存在',404)
        node=nodes[0]
        if current.get('revision')!=revision:raise APIError('REVISION_CONFLICT','任务状态已更新，请用当前决策重新查询',409)
        if (current.get('mode')!='real' or current.get('engineState')!='deciding' or current.get('cancelRequested')
                or current.get('currentNodeId')!=node_id):
            raise APIError('HISTORY_DECISION_SCOPE','仅当前已授权的真实模型决策可查询此入口',409)
        bound=capability(current,node)
        output={'schema':RESULT_SCHEMA,'binding':bound,'status':'available','notice':NOTICE,'cases':[],
            'projectId':'qcal-local-f8-platform','projectRoot':str(root),
            'limits':{'maximumCases':MAX_CASES,'maximumCandidates':MAX_CANDIDATES,'maximumBytes':MAX_BYTES,'maximumHashedBytes':MAX_HASH_BYTES},
            'examined':0,'excluded':[],'readOnly':True,'currentCalibration':False,'executionAuthorized':False,'checkedAt':now()}
        try:bindings=load_audited_bindings(root,IMPLEMENTATION)
        except (APIError,OSError,ValueError,TypeError,KeyError):bindings={}
        current_binding=bindings.get(current.get('adapterId'))
        if not bound or not current_binding or current.get('adapterProfileHash')!=current_binding['fingerprint']:
            return {**output,'status':'unavailable','reasonCode':'consumer_receipt_schema_not_audited'}
        candidates=db.execute("SELECT id,workflow_id FROM platform_attempts WHERE json_extract(payload,'$.procedureId')=? "
            "AND json_extract(payload,'$.state')='verified' AND json_extract(payload,'$.mode')='real' ORDER BY rowid DESC LIMIT ?",
            (node['procedureId'],MAX_CANDIDATES)).fetchall()
        budget=[MAX_HASH_BYTES];checked=set();seen_raw=set();workflows={}
        for candidate in candidates:
            if len(output['cases'])>=limit:break
            output['examined']+=1
            try:
                if candidate['workflow_id'] not in workflows:
                    r=db.execute('SELECT payload FROM platform_workflows WHERE id=?',(candidate['workflow_id'],)).fetchone()
                    require(r is not None,'source_workflow_missing');workflows[candidate['workflow_id']]=_object(r[0])
                source=workflows[candidate['workflow_id']]
                attempt=_object(db.execute('SELECT payload FROM platform_attempts WHERE id=?',(candidate['id'],)).fetchone()[0])
                binding=bindings.get(source.get('adapterId'))
                require(binding and source.get('adapterProfileHash')==binding['fingerprint'],'source_profile_not_audited')
                _source_files(root,binding,checked)
                case=_case(db,root,current,node,current_binding,source,attempt,binding,budget)
                raw={ref['sha256'] for op in case['operations'] for ref in op['sourceReferences'] if ref['name']=='raw_scan.npz'}
                require(raw.isdisjoint(seen_raw),'duplicate_historical_raw')
                candidate_output={**output,'cases':output['cases']+[case]}
                require(len(dumps(candidate_output).encode('utf8'))<=MAX_BYTES-1024,'response_budget')
                output=candidate_output;seen_raw.update(raw)
            except (APIError,OSError,ValueError,TypeError,KeyError,StopIteration,IndexError) as exc:
                code=str(exc) if type(exc) is ValueError and str(exc).replace('_','').isalnum() and len(str(exc))<80 else 'source_validation_failed'
                if len(output['excluded'])<8:output['excluded'].append({'sourceAttempt':candidate['id'],'reasonCode':code})
                if code in ('response_budget','archive_read_budget'):break
        output['bytesHashedUpperBound']=MAX_HASH_BYTES-budget[0]
    # A pause/edit while files were hashed invalidates the answer, even though it is read-only.
    with read_chat_snapshot(db_path) as db:
        latest=db.execute('SELECT revision FROM platform_workflows WHERE id=?',(workflow_id,)).fetchone()
        if not latest or latest[0]!=revision:raise APIError('REVISION_CONFLICT','查询期间任务已更新，请重新读取',409)
    require(len(dumps(output).encode('utf8'))<=MAX_BYTES,'response_budget')
    return output
