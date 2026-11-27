"""Explicit, single-attempt execution versions. Workflow identity never changes.

The authorization is a command ledger entry, not a calibration or a run permit.
Only the existing manual-step command may consume it, atomically with the first
attempt/effect/lease. Historical attempts select their own immutable binding.
"""
from copy import copy, deepcopy
import json

from .common import APIError, checked_path, digest, dumps, ident, now

SCHEMA = 'qcal-attempt-execution-authorization/1'
BINDING = 'qcal-attempt-execution-binding/1'
VERSION = 'rabi16-v1'
BASE_ID = 'arclight-private-f8-q11-v3'
ADAPTER_ID = 'arclight-private-f8-q11-rabi16-v1'
PROFILE = 'adapters/arclight_profile_rabi16_v1.json'


def require(value, message, code='EXECUTION_VERSION_SCOPE'):
    if not value:
        raise APIError(code, message, 409)


def _base(c, workflow_id, rt=None):
    original = getattr(c, '_execution_base', c)
    return original.for_workflow(workflow_id, rt)


def _adapter(base):
    from adapters.arclight_rabi16_adapter import ArcLightRabi16Adapter
    from adapters.arclight_rabi16_runtime.policy import load_profile
    # This is a compiled-in allowlist path, never a client-supplied profile.
    return ArcLightRabi16Adapter(base.adapter.root, load_profile(base.adapter.root))


def _context(parameters):
    from adapters.arclight_rabi16_runtime.policy import parameter_context_sha256
    return parameter_context_sha256(parameters)


def _record(wf, authorization_id):
    record = (wf.get('attemptExecutionAuthorizations') or {}).get(authorization_id)
    require(isinstance(record, dict) and isinstance(record.get('authorization'), dict),
            '本次执行版本缺少已保存的授权')
    auth = record['authorization']
    require(record.get('sha256') == digest(auth) and auth.get('schema') == SCHEMA
        and auth.get('id') == authorization_id and auth.get('workflowId') == wf['id']
        and auth.get('baseAdapterId') == wf.get('adapterId') == BASE_ID
        and auth.get('baseAdapterProfileHash') == wf.get('adapterProfileHash'),
        '执行版本授权或原任务身份已改变')
    return record, auth


def _validate(base, wf, node, record, auth, *, next_attempt=False):
    if auth.get('executionVersion') == 'smart-rabi16-v1':
        from .smart_sampling import validate_authorization
        return validate_authorization(base,wf,node,record,auth,next_attempt=next_attempt)
    base._assert_adapter_binding(wf)
    require(wf.get('mode') == 'real' and auth.get('policy') == 'strict'
        and not wf.get('configurationBinding') and auth.get('nodeId') == node['id']
        and auth.get('procedureId') == node['procedureId'] == 'sq_rabi_amplitude'
        and auth.get('resourceId') == node['resourceId'] == 'Q11'
        and auth.get('executionVersion') == VERSION and auth.get('adapterId') == ADAPTER_ID,
        '执行版本只适用于已选择节点的一次幅度 Rabi')
    if next_attempt:
        require(wf.get('policy') == 'strict' and record.get('state') == 'selected' and auth.get('attemptNumber') == node.get('attempt', 0) + 1,
                '本次执行版本已经使用或不属于下一次尝试')
    adapter = _adapter(base)
    bound = copy(base)
    bound.adapter, bound.profile = adapter, adapter.profile
    bound.adapter_id = adapter.profile['id']
    bound.adapter_profile_hash = bound._adapter_fingerprint()
    require(bound.adapter_profile_hash == auth.get('adapterProfileHash'),
            '本次选择的执行版本或源码已经改变', 'ADAPTER_CHANGED')
    bound._execution_base = base
    bound._attempt_execution_authorization = deepcopy(auth)
    bound._attempt_execution_record_sha = record['sha256']
    bound._ownership_cache = None
    return bound


def for_node(c, workflow_id, node_id, rt=None, *, purpose='next', attempt=None):
    if rt is None:
        with c.store.connect() as reader:
            return for_node(c, workflow_id, node_id, reader, purpose=purpose, attempt=attempt)
    base = _base(c, workflow_id, rt)
    wf = base.get(workflow_id, rt); node = base._node(wf, node_id)
    binding = attempt.get('executionBinding') if isinstance(attempt, dict) else None
    if binding:
        record, auth = _record(wf, binding.get('authorizationId'))
        require(record.get('state') == 'consumed' and record.get('attemptId') == attempt.get('id')
            and attempt.get('nodeId') == node_id and attempt.get('number') == auth.get('attemptNumber'),
            '归档尝试与其执行版本授权不一致')
        bound = _validate(base, wf, node, record, auth)
        require(binding == make_binding(bound, attempt['id'], stage='acquisition'),
                '归档尝试的执行版本绑定已改变')
    else:
        authorization_id = node.get('nextExecutionAuthorizationId')
        if not authorization_id:
            return base
        record, auth = _record(wf, authorization_id)
        if record.get('state') != 'selected':
            if purpose == 'view' and node.get('latestAttemptId') == record.get('attemptId'):
                row = rt.db.execute('SELECT payload FROM platform_attempts WHERE id=? AND workflow_id=? AND node_id=?',
                    (record['attemptId'], wf['id'], node_id)).fetchone()
                require(row is not None, '执行版本缺少对应尝试')
                # Retain the caller so an already authenticated detail/preview controller
                # can be reused after resolving this consumed historical authorization.
                return for_node(c, workflow_id, node_id, rt, attempt=json.loads(row[0]))
            return base
        bound = _validate(base, wf, node, record, auth, next_attempt=True)
        parameters, versions = base._parameters(rt, wf, node)
        current = digest(parameters) == auth['selectionParametersSha256'] and digest(versions) == auth['parameterVersionsSha256']
        if purpose == 'view':
            # Keep the normal detail/cancel entry reachable; this never supplies
            # a runnable plan for a superseded parameter snapshot.
            bound._attempt_execution_stale = not current
        else:
            require(current, '参数或上游版本已改变，请重新选择本次执行版本', 'EXECUTION_VERSION_PARAMETERS_CHANGED')
    old = getattr(c, '_attempt_execution_authorization', None)
    same_view = getattr(c, '_attempt_execution_stale', False) == getattr(bound, '_attempt_execution_stale', False)
    return c if old == auth and same_view and getattr(c, 'adapter_profile_hash', None) == bound.adapter_profile_hash else bound


def make_binding(c, attempt_id, *, stage):
    auth = c._attempt_execution_authorization
    binding = {'schema': BINDING, 'stage': stage, 'authorizationId': auth['id'],
        'authorizationSha256': c._attempt_execution_record_sha,
        **{k: auth[k] for k in ('baseAdapterId','baseAdapterProfileHash','adapterId','adapterProfileHash',
            'workflowId','nodeId','procedureId','resourceId','attemptNumber','parametersSha256')},
        'attemptId': attempt_id, 'selectionOperationId': attempt_id + '/measure',
        'verificationOperationId': attempt_id + '/verify'}
    if auth.get('executionVersion') == 'smart-rabi16-v1':
        from adapters.arclight_smart_rabi16_runtime.policy import BINDING_SCHEMA
        binding.update(schema=BINDING_SCHEMA,**{k:auth[k] for k in ('smartRunSha256','sourceAttemptId','sourceEvidenceSha256')})
    return binding


def attach_plan(c, request):
    if getattr(c, '_attempt_execution_authorization', None):
        require(not getattr(c,'_attempt_execution_stale',False),
                '参数或上游版本已改变，请恢复原执行版本后重新选择', 'EXECUTION_VERSION_PARAMETERS_CHANGED')
        require(_context(request['parameters']) == c._attempt_execution_authorization['parametersSha256'],
                '本次执行版本的参数已改变', 'EXECUTION_VERSION_PARAMETERS_CHANGED')
        request['executionBinding'] = make_binding(c, request['attemptId'], stage='plan_only')


def assert_binding(c, wf):
    base = c._execution_base
    auth = c._attempt_execution_authorization
    record, current = _record(wf, auth['id'])
    require(current == auth, '执行版本授权已经改变')
    _validate(base, wf, base._node(wf, auth['nodeId']), record, auth)
    require(c._adapter_fingerprint() == c.adapter_profile_hash, '执行版本源码已改变', 'ADAPTER_CHANGED')


def claim(c, rt, wf, node, attempt, request):
    if not getattr(c, '_attempt_execution_authorization', None):
        return
    record, auth = _record(wf, c._attempt_execution_authorization['id'])
    require(record['state'] == 'selected' and attempt['number'] == auth['attemptNumber']
        and digest(request['parameters']) == auth['selectionParametersSha256']
        and digest(request['parameterVersions']) == auth['parameterVersionsSha256'],
        '本次执行版本已使用、参数变化或尝试不匹配')
    binding = make_binding(c, attempt['id'], stage='acquisition')
    record.update(state='consumed', attemptId=attempt['id'], consumedAt=now())
    attempt['executionBinding'] = deepcopy(binding)
    request['executionBinding'] = deepcopy(binding)


def validate_request(c, rt, request, *, live=False):
    wf = c.get(request['runId'], rt)
    row = rt.db.execute('SELECT payload FROM platform_attempts WHERE id=? AND workflow_id=? AND node_id=?',
        (request['attemptId'], wf['id'], request['nodeId'])).fetchone()
    require(row is not None, '执行版本缺少当前尝试')
    attempt = json.loads(row[0])
    bound = for_node(c, wf['id'], request['nodeId'], rt, attempt=attempt)
    require(request.get('executionBinding') == attempt.get('executionBinding')
        and request.get('adapterId') == bound.adapter_id and request.get('adapterProfileHash') == bound.adapter_profile_hash
        and request['operationId'] in (attempt['id']+'/measure', attempt['id']+'/verify')
        and _context(request['parameters']) == attempt['executionBinding']['parametersSha256'],
        '执行请求与本次尝试冻结版本不一致')
    if request['operationId'].endswith('/measure'):
        require(digest(request['parameters']) == bound._attempt_execution_authorization['selectionParametersSha256'],
                '首测参数已偏离本次版本授权')
    if live:
        node = c._node(wf, request['nodeId'])
        lease = rt.db.execute('SELECT token,state FROM platform_leases WHERE workflow_id=? AND attempt_id=?',
            (wf['id'], attempt['id'])).fetchone()
        effect = rt.db.execute('SELECT request,state FROM platform_effects WHERE workflow_id=? AND attempt_id=? AND operation_id=?',
            (wf['id'], attempt['id'], request['operationId'])).fetchone()
        require(node.get('latestAttemptId') == attempt['id'] and lease is not None and lease['state']=='active'
            and lease['token']==request.get('leaseToken') and effect is not None and effect['state']=='pending'
            and json.loads(effect['request'])==request, '执行版本没有当前租约及待执行回执')
        if bound._attempt_execution_authorization.get('executionVersion') == 'smart-rabi16-v1':
            from .smart_sampling import assert_live
            assert_live(bound,rt,wf,node,bound._attempt_execution_authorization)
        else:
            c._assert_manual_dispatch(wf, node['id'], attempt['id'])
    return bound


def summary(c, wf, node):
    identifier = node.get('nextExecutionAuthorizationId')
    if identifier:
        record, auth = _record(wf, identifier)
        return {'executionVersion': auth['executionVersion'], 'label':'幅度 Rabi · 每点最多 16 块',
            'state':record['state'], 'nextAttemptNumber':auth['attemptNumber'],
            'adapterId':auth['adapterId'], 'adapterProfileHash':auth['adapterProfileHash'],
            'authorizationId':auth['id'], 'scope':'仅指定尝试的首测及独立复测；不是启动许可'}
    return None


def sampling_evidence(c, rt, request, result, *, plan=None, completion=None):
    """Authenticate this version's complete, DB-registered block receipt.

    The binding alone is never a measurement proof. Historical base adapters
    keep their original authentication, with no new fields or file requirements.
    """
    bound = validate_request(c, rt, request)
    from adapters.arclight_rabi16_runtime import policy
    from adapters.arclight_rabi16_runtime.execute_worker import validate_sampling_receipt
    directory = checked_path(bound.adapter._operation_dir(request['operationId']))
    try:
        if plan is None:
            plan = json.loads((directory/'plan.json').read_text(encoding='utf-8'))
        if completion is None:
            completion = json.loads((directory/'completion.json').read_text(encoding='utf-8'))
        require(plan == bound.adapter.build_plan(request), '分块证据的计划与冻结请求不一致')
        inventory = result.get('artifacts')
        require(isinstance(inventory,list) and 0 < len(inventory) <= policy.MAX_REGISTERED_ARTIFACTS,
            '分块证据登记数量无效')
        registered, seen = [], set()
        for artifact in inventory:
            identifier = artifact.get('id')
            require(isinstance(identifier,str) and identifier not in seen, '分块证据登记身份重复')
            seen.add(identifier)
            row = rt.db.execute('SELECT path,sha256,payload FROM platform_artifacts WHERE id=?',(identifier,)).fetchone()
            require(row is not None and row['sha256']==artifact.get('sha256'), '分块证据缺少原始登记')
            metadata = json.loads(row['payload'])
            path = checked_path(row['path'],parent=directory)
            require(all(metadata.get(k)==artifact.get(k) for k in ('id','name','kind','sha256'))
                and path.name==artifact.get('name')
                and checked_path(artifact.get('sourcePath'),parent=directory)==path,
                '分块证据的登记路径或身份不一致')
            registered.append(metadata)
        evidence = validate_sampling_receipt(directory,plan,completion,registered_artifacts=registered)
        require(result.get('provenance',{}).get('executionBinding')==request['executionBinding']
            and result['provenance'].get('samplingPolicy')==policy.POLICY
            and result['provenance'].get('samplingReceipt')==completion.get('samplingReceipt'),
            '采集结果与分块完成回执不一致')
        if bound._attempt_execution_authorization.get('executionVersion') == 'smart-rabi16-v1':
            from adapters.arclight_smart_rabi16_runtime.policy import EXECUTION_POLICY
            require(result.get('provenance',{}).get('executionPolicy') == EXECUTION_POLICY
                and plan.get('executionPolicy') == EXECUTION_POLICY,
                '智能采样执行策略与实际计划不一致')
        return evidence
    except APIError:
        raise
    except (OSError,ValueError,TypeError,KeyError) as exc:
        raise APIError('EXECUTION_VERSION_EVIDENCE','分块采集证据无法核对：'+str(exc),409) from None


def select(c, workflow_id, node_id, payload):
    base = _base(c, workflow_id)
    with base.store.transaction() as rt:
        wf = base.get(workflow_id, rt)
        existing, request_hash = base._command(rt, wf, {**payload,'nodeId':node_id}, 'execution_version')
        if existing:return existing
        require(set(payload) == {'operationId','expectedRevision','executionVersion','reason'}, '执行版本命令包含未知字段')
        require(payload['executionVersion'] in (VERSION,'default') and isinstance(payload['reason'],str)
            and 1 <= len(payload['reason'].strip()) <= 1000, '请选择受支持的版本并填写原因')
        base._manual_idle(rt, wf)
        require(wf.get('policy')=='strict' and wf.get('adapterId')==BASE_ID
            and not wf.get('configurationBinding'), '仅固定 Q11 人工逐步任务支持本次版本选择')
        base._assert_adapter_binding(wf)
        node = base._node(wf,node_id)
        require(node['procedureId']=='sq_rabi_amplitude' and node['resourceId']=='Q11'
            and node['engineState'] not in ('verified','awaiting_acceptance')
            and wf.get('currentNodeId') in (None,node_id), '请选择当前尚未验收的幅度 Rabi 步骤')
        previous_id = node.get('nextExecutionAuthorizationId')
        previous = _record(wf,previous_id)[0] if previous_id else None
        if payload['executionVersion']=='default':
            require(previous is not None, '当前没有可取消的执行版本')
            if previous['state']=='selected':previous.update(state='cancelled',cancelledAt=now())
            node.pop('nextExecutionAuthorizationId',None)
            node.setdefault('parameterOverrides',{})['acquisitions_per_point']=min(8,
                previous['authorization']['previousBlocks'])
        else:
            parameters, versions = base._parameters(rt,wf,node)
            previous_blocks = parameters.get('acquisitions_per_point',1)
            parameters = {**parameters,'acquisitions_per_point':16}
            require(parameters.get('shots')==2000, '16 块版本每块固定使用 2000 shots')
            adapter = _adapter(base); bound=copy(base)
            bound.adapter,bound.profile=adapter,adapter.profile;bound.adapter_id=ADAPTER_ID
            bound.adapter_profile_hash=bound._adapter_fingerprint()
            authorization={'schema':SCHEMA,'id':ident('execution-version'),'executionVersion':VERSION,
                'policy':'strict',
                'workflowId':wf['id'],'nodeId':node_id,'procedureId':node['procedureId'],'resourceId':node['resourceId'],
                'attemptNumber':node['attempt']+1,'baseAdapterId':wf['adapterId'],'baseAdapterProfileHash':wf['adapterProfileHash'],
                'adapterId':ADAPTER_ID,'adapterProfileHash':bound.adapter_profile_hash,
                'selectionParametersSha256':digest(parameters),'parameterVersionsSha256':digest(versions),
                'parametersSha256':_context(parameters),'previousBlocks':previous_blocks,
                'requestedRevision':wf['revision'],'reason':payload['reason'].strip(),'createdAt':now()}
            record={'authorization':authorization,'sha256':digest(authorization),'state':'selected'}
            wf.setdefault('attemptExecutionAuthorizations',{})[authorization['id']]=record
            node['nextExecutionAuthorizationId']=authorization['id']
            node.setdefault('parameterOverrides',{})['acquisitions_per_point']=16
            if previous and previous['state']=='selected':previous.update(state='superseded',supersededAt=now())
            bound=_validate(base,wf,node,record,authorization,next_attempt=True)
            bound._validate_plan(wf,node,parameters,versions,payload['operationId'],joint_budget=True,rt=rt)
        parameters,_=base._parameters(rt,wf,node)
        node['parameters']=parameters
        affected=base._invalidate(wf,node_id,include_target=True)
        wf.update(status='paused',engineState='parameters_updated')
        base._record_decision(rt,wf,node_id,{'action':'execution_version_selected',
            'reason':payload['reason'].strip(),'evidenceIds':[],'executionVersion':payload['executionVersion'],
            'invalidatedNodes':affected},'operator')
        base.store.save(rt,wf,'execution_version_selected',{'nodeId':node_id,
            'message':'已保存下一次尝试的执行版本；尚未启动采集','executionVersion':payload['executionVersion']})
        rt.db.execute('INSERT INTO platform_commands VALUES(?,?,?)',(payload['operationId'],request_hash,dumps(wf)))
        return wf
