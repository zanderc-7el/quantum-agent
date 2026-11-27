"""Evidence-bound 8 -> 16 block replanning for the explicitly launched smart Q11.

No run permit is issued here. Existing controller decisions, attempts, leases,
budgets and independent verification remain the only execution path.
"""
from copy import copy, deepcopy
import json
from .common import APIError, digest, ident, now
from adapters.arclight_smart_rabi16_runtime import policy

VERSION = 'smart-rabi16-v1'
START_SCOPE = 'q11-full-same-gate/1'
WORKPOINT = ('drive_hz','drive_length_s','readout_hz','readout_amp','readout_width_s',
             'xy_delay_s','xy_attenuation_db','xy_filter_mhz','pulse_compatibility')

def require(value,message):
    if not value: raise APIError('SMART_SAMPLING_EVIDENCE_REQUIRED',message,409)

def run_identity(wf):
    start=wf.get('smartRun') or {}
    return {k:start.get(k) for k in ('schema','operationId','stationConfirmedAt','adapterProfileHash')}

def eligible(wf,node):
    return (wf.get('mode')=='real' and wf.get('policy')=='autopilot' and wf.get('resourceIds')==['Q11']
        and wf.get('adapterId')==policy.BASE_ID and wf.get('adapterProfileHash')==policy.BASE_FINGERPRINT
        and not wf.get('configurationBinding') and wf.get('coverage')=='full' and len(wf.get('nodes',[]))==13
        and wf.get('budget',{}).get('mode')=='accounting_only' and wf.get('hardwareConfirmed') is True
        and wf.get('smartRun',{}).get('schema')==START_SCOPE
        and wf['smartRun'].get('adapterProfileHash')==wf['adapterProfileHash']
        and isinstance(wf['smartRun'].get('operationId'),str) and bool(wf['smartRun']['operationId'])
        and bool(wf['smartRun'].get('stationConfirmedAt'))
        and node.get('resourceId')=='Q11' and node.get('procedureId')=='sq_rabi_amplitude')

def _base(c,wf,rt=None): return getattr(c,'_execution_base',c).for_workflow(wf['id'],rt)

def _source(c,rt,wf,node):
    """Reuse the existing native receipt/plan/raw authenticator, also for failure."""
    require(eligible(wf,node),'仅已明确启动的完整智能Q11任务可使用智能16块能力')
    require(rt.db.execute('SELECT 1 FROM platform_accounting_workflows WHERE workflow_id=? AND parent_id IS NULL',(wf['id'],)).fetchone(),
            '智能任务缺少同事务计量登记')
    require(not c._has_lease(rt,wf['id']) and not rt.db.execute("SELECT 1 FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')",(wf['id'],)).fetchone(),
            '当前采集回执或租约尚未结束')
    row=rt.db.execute('SELECT payload FROM platform_attempts WHERE id=? AND workflow_id=? AND node_id=?',
        (node.get('latestAttemptId'),wf['id'],node['id'])).fetchone()
    require(row is not None,'缺少当前弱响应尝试')
    attempt=json.loads(row[0]);result=attempt.get('result') or {}
    require(attempt.get('state')=='attention' and attempt.get('endedAt') and not attempt.get('error')
        and attempt.get('mode')=='real' and attempt.get('procedureId')=='sq_rabi_amplitude'
        and attempt.get('number')==node.get('attempt') and result==node.get('result')
        and result.get('dataKind')=='real' and result.get('quality',{}).get('status')=='failed'
        and not any(result.get(k) for k in ('partial','stale','verificationPending')),
        '仅完整、当前、已关闭的真实失败响应可作为加块依据')
    operations=[attempt.get('operationId')]
    if attempt.get('verificationOperationId'): operations.append(attempt['verificationOperationId'])
    require(operations==[attempt['id']+'/measure'] or operations==[attempt['id']+'/measure',attempt['id']+'/verify'],
            '来源首复测身份不完整')
    from .gate_calibration_context import _operation
    proofs=[];raw_seen=set();native=None
    for index,op in enumerate(operations):
        native,raw=_operation(c,rt,wf,node,attempt,op,'selection' if index==0 else 'verification')
        require(not raw_seen.intersection(raw),'首复测重复使用原始数据');raw_seen.update(raw)
        proofs.append({'operationId':op,'resultSha256':digest(native),'rawSha256':sorted(raw)})
    assert native is not None
    for key in ('parameters','metrics','series','analysisQuality','provenance','acquiredAt','sourceRunId'):
        require(native.get(key)==result.get(key),'来源主结果与已登记原生回执不一致')
    params=native['parameters'];blocks=params.get('acquisitions_per_point')
    require(blocks in (8,16) and params.get('shots')==2000 and params.get('capture_repeats',0)>=2,
            '须先有同工况8块完整实际响应；不能把其它采样伪作8块')
    if blocks==16:
        require(attempt.get('executionBinding',{}).get('adapterId')==policy.ADAPTER_ID,
                '继续智能16块须有本智能策略的实际前次回执')
    analysis=native.get('analysisQuality') or {};reasons=analysis.get('reasons') or []
    weak=analysis.get('accepted') is False and any(
        phrase in reason.lower() for reason in reasons if isinstance(reason,str)
        for phrase in ('not resolved relative to residual/shot noise','insufficient bic improvement','valid repeat fits are required'))
    require(weak,'当前失败原因没有已记录的弱振荡或重复拟合不足依据；不能自动增加shots解释其它故障')
    effective,versions=c._parameters(rt,wf,node)
    require(all(k in params and c._same_parameter(params[k],effective.get(k)) for k in WORKPOINT),
            '当前驱动、读出或门条件与弱响应来源不同')
    require(attempt.get('parameterVersions')==versions,'来源后参数版本已改变，请先重新取得当前响应')
    return {'sourceAttemptId':attempt['id'],'sourceAttemptSha256':digest(attempt),
        'sourceParametersSha256':digest(params),'sourceBlocks':blocks,'operations':proofs,
        'reasons':[str(x)[:512] for x in reasons[:8]],'workpoint':{k:params[k] for k in WORKPOINT}}

def model_bounds(c,wf,node,bounds):
    result=deepcopy(bounds)
    if not eligible(wf,node): return result
    try:
        with c.store.connect() as rt:
            current=c.get(wf['id'],rt)
            require(current['revision']==wf['revision'],'模型输入版本已改变')
            _source(_base(c,current,rt),rt,current,c._node(current,node['id']))
    except (APIError,OSError,ValueError,KeyError,TypeError): return result
    if 'acquisitions_per_point' in result:
        result['acquisitions_per_point']={**result['acquisitions_per_point'],'max':16}
    return result

def adapter(base):
    from adapters.arclight_smart_rabi16_adapter import ArcLightSmartRabi16Adapter
    return ArcLightSmartRabi16Adapter(base.adapter.root,policy.load_profile(base.adapter.root))

def validate_authorization(base,wf,node,record,auth,*,next_attempt=False):
    base._assert_adapter_binding(wf)
    require(eligible(wf,node) and auth.get('policy')=='autopilot' and auth.get('executionVersion')==VERSION
        and auth.get('adapterId')==policy.ADAPTER_ID and auth.get('nodeId')==node['id']
        and auth.get('procedureId')==node['procedureId'] and auth.get('resourceId')==node['resourceId']
        and auth.get('smartRunSha256')==digest(run_identity(wf))
        and auth.get('sourceEvidenceSha256')==digest(auth.get('sourceEvidence'))
        and auth.get('sourceAttemptId')==auth.get('sourceEvidence',{}).get('sourceAttemptId'),
        '智能16块授权或原父任务身份不一致')
    if next_attempt:
        require(record.get('state')=='selected' and auth.get('attemptNumber')==node.get('attempt',0)+1,
                '智能16块授权不是实际下一尝试')
    bound=copy(base);bound.adapter=adapter(base);bound.profile=bound.adapter.profile
    bound.adapter_id=policy.ADAPTER_ID;bound.adapter_profile_hash=bound._adapter_fingerprint()
    require(bound.adapter_profile_hash==auth.get('adapterProfileHash'),'智能16块源码版本已改变')
    bound._execution_base=base;bound._attempt_execution_authorization=deepcopy(auth)
    bound._attempt_execution_record_sha=record['sha256'];bound._ownership_cache=None
    return bound

def authorize_retry(c,rt,wf,node,trial,versions,decision):
    """Prepare only. Caller must validate plan then explicitly commit_retry."""
    if trial.get('acquisitions_per_point',1)<=8:
        # A completed versioned attempt must not trap later replanning inside
        # its 16-only adapter. The ordinary v3 path retains its original rules.
        return _base(c,wf,rt) if getattr(c,'_attempt_execution_authorization',{}).get('executionVersion')==VERSION else c
    if trial.get('acquisitions_per_point')!=16 or trial.get('shots')!=2000:
        raise APIError('PARAMETER_OUT_OF_RANGE','智能扩展仅允许16个独立2000-shot块，不支持9–15或超限块数')
    base=_base(c,wf,rt);proof=_source(base,rt,wf,node)
    if not all(base._same_parameter(trial.get(k),proof['workpoint'][k]) for k in WORKPOINT):
        raise APIError('BAD_PARAMETERS','加块诊断须保持同一驱动、门长及读出工作点；改变工况请先使用原8块路径取得新响应')
    actual=adapter(base);auth={'schema':'qcal-attempt-execution-authorization/1','id':ident('smart-sampling'),
        'executionVersion':VERSION,'policy':'autopilot','workflowId':wf['id'],'nodeId':node['id'],
        'procedureId':node['procedureId'],'resourceId':node['resourceId'],'attemptNumber':node['attempt']+1,
        'baseAdapterId':wf['adapterId'],'baseAdapterProfileHash':wf['adapterProfileHash'],
        'adapterId':policy.ADAPTER_ID,'adapterProfileHash':policy.fingerprint(actual.root,actual.profile),
        'selectionParametersSha256':digest(trial),'parameterVersionsSha256':digest(versions),
        'parametersSha256':policy.parameter_context_sha256(trial),'previousBlocks':proof['sourceBlocks'],
        'smartRunSha256':digest(run_identity(wf)),'sourceAttemptId':proof['sourceAttemptId'],
        'sourceEvidence':proof,'sourceEvidenceSha256':digest(proof),'decisionSha256':digest(decision),
        'requestedRevision':wf['revision'],'reason':str(decision.get('reason',''))[:1000],'createdAt':now()}
    record={'authorization':auth,'sha256':digest(auth),'state':'selected'}
    bound=validate_authorization(base,wf,node,record,auth,next_attempt=True)
    bound._prepared_smart_authorization=record
    return bound

def commit_retry(wf,node,planned):
    record=getattr(planned,'_prepared_smart_authorization',None)
    if record is None:return
    auth=record['authorization'];require(auth['workflowId']==wf['id'] and auth['nodeId']==node['id']
        and auth['attemptNumber']==node['attempt']+1,'智能授权提交目标已改变')
    previous=node.get('nextExecutionAuthorizationId')
    prior=wf.get('attemptExecutionAuthorizations',{}).get(previous)
    if prior and prior.get('state')=='selected':prior.update(state='superseded',supersededAt=now())
    wf.setdefault('attemptExecutionAuthorizations',{})[auth['id']]=deepcopy(record)
    node['nextExecutionAuthorizationId']=auth['id']

def assert_live(c,rt,wf,node,auth):
    require(eligible(wf,node) and wf.get('status')=='running' and not wf.get('cancelRequested')
        and auth['smartRunSha256']==digest(run_identity(wf)), '智能父任务已停止或授权变化')
    source=rt.db.execute('SELECT payload FROM platform_attempts WHERE id=? AND workflow_id=? AND node_id=?',
        (auth['sourceAttemptId'],wf['id'],node['id'])).fetchone()
    require(source is not None and digest(json.loads(source[0]))==auth['sourceEvidence']['sourceAttemptSha256'],
            '智能采样来源尝试已改变')
    prior=json.loads(source[0]);source_node={**node,'attempt':prior['number'],
        'latestAttemptId':prior['id'],'result':prior['result']}
    from .gate_calibration_context import _operation
    for index,proof in enumerate(auth['sourceEvidence']['operations']):
        native,raw=_operation(_base(c,wf,rt),rt,wf,source_node,prior,proof['operationId'],
                              'selection' if index==0 else 'verification')
        require(digest(native)==proof['resultSha256'] and sorted(raw)==proof['rawSha256'],
                '下发前弱响应来源原始回执已改变')
