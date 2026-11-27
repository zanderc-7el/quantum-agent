"""Evidence-bound fixed-DC window exploration for the opted-in smart Q11.

This uses the existing v3 4 ns..2 us capability at eight blocks. It does not
modify a native profile, infer a period from rejected fits, or extend the
single-attempt strict Rabi16 authorization. All new windows need fresh data.
"""
from copy import deepcopy
import json
import math
from .common import APIError, digest, ident, now, number
from . import smart_sampling

SCHEMA='qcal-smart-rabi-window/1'
PROPOSAL_POLICY='fixed_dc_window_actual_changes_v2'
CYCLE_REASON='Fitted oscillation contains fewer than 1.5 cycles.'
FIXED=('drive_hz','readout_hz','readout_amp','readout_width_s','xy_delay_s',
       'xy_attenuation_db','xy_filter_mhz','pulse_compatibility')
IGNORED=('drive_amp','half_pi_amp','order_seed')

def require(value,message):
    if not value:raise APIError('SMART_RABI_WINDOW_EVIDENCE_REQUIRED',message,409)

def _context(parameters):return digest({k:v for k,v in parameters.items() if k not in IGNORED})

def _f01(c,rt,wf,node,parameters,versions):
    from .gate_calibration_context import _ancestors, _operation
    from .quality import evaluate
    nodes=[n for n in _ancestors(wf,node) if n.get('procedureId')=='sq_spectroscopy']
    require(len(nodes)==1,'缺少唯一同任务定频来源')
    prior=nodes[0]
    row=rt.db.execute('SELECT payload FROM platform_attempts WHERE workflow_id=? AND node_id=? AND id=?',
        (wf['id'],prior['id'],prior.get('latestAttemptId'))).fetchone()
    require(row is not None,'缺少当前定频尝试')
    attempt=json.loads(row[0]);result=attempt.get('result') or {}
    require(prior.get('engineState')=='verified' and attempt.get('state')=='verified'
        and attempt.get('mode')=='real' and attempt.get('endedAt') and not attempt.get('error')
        and attempt.get('number')==prior.get('attempt') and result==prior.get('result')
        and result.get('quality',{}).get('status')=='passed'
        and result['quality'].get('criteria')==wf['gates']['sq_spectroscopy']
        and result['quality'].get('ruleVersion')==wf['gatesVersion']
        and not any(result.get(k) for k in ('partial','stale','verificationPending')),
        '定频尚未独立验收或已经失效')
    operations=(attempt.get('operationId'),attempt.get('verificationOperationId'))
    require(operations==(attempt['id']+'/measure',attempt['id']+'/verify'),'定频首复测身份不完整')
    selection,raw1=_operation(c,rt,wf,prior,attempt,operations[0],'定频首测')
    verification,raw2=_operation(c,rt,wf,prior,attempt,operations[1],'定频复测')
    require(not raw1&raw2,'定频首复测原始数据不独立')
    for stage,original in ((result.get('selectionResult',{}),selection),(result,verification)):
        require(all(stage.get(k)==original.get(k) for k in ('parameters','metrics','series','artifacts','sourceRunId','acquiredAt')),
            '定频验收与原生记录不同')
    selected=c._normalize_candidate('sq_spectroscopy',selection.get('candidateParameters',{}))
    repeated=c._normalize_candidate('sq_spectroscopy',verification.get('candidateParameters',{}))
    require(selected==result.get('candidateParameters') and number(selected.get('drive_hz'))
        and result.get('verificationCandidateParameters')==verification.get('candidateParameters')
        and not c._repeatability(selected,repeated,selection['parameters']), '定频首复候选不一致')
    for original in (selection,verification):
        check=evaluate(original,'sq_spectroscopy','real',wf['gates'],independent=True,rule_version=wf['gatesVersion'])
        require(check['quality']['status']=='passed','定频原始数值不满足冻结门槛')
        expected={**original['parameters'],'drive_hz':selected['drive_hz']}
        require(all(c._same_parameter(parameters.get(k),expected.get(k)) for k in FIXED),
            '定频的驱动或读出条件与本次幅度扫描不同')
    for key in FIXED:
        require(versions.get(key,0)==prior.get('parameterVersions',{}).get(key,0),
            '当前驱动或读出版本与定频验收不同')
    return {'nodeId':prior['id'],'attemptId':attempt['id'],'attemptSha256':digest(attempt),
        'operations':[{'operationId':op,'resultSha256':digest(original),'rawSha256':sorted(raw)}
            for op,original,raw in ((operations[0],selection,raw1),(operations[1],verification,raw2))]}

def _proof(c,rt,wf,node):
    require(smart_sampling.eligible(wf,node),'仅已授权的完整智能 Q11 幅度节点可探索时窗')
    c._assert_adapter_binding(wf)
    failed=smart_sampling._source(c,rt,wf,node)
    result=node['result'];analysis=result.get('analysisQuality') or {}
    groups=analysis.get('groups') or []
    require(analysis.get('kind')=='rabi_amplitude' and len(groups)==1
        and groups[0].get('repeat_validated') is False,'缺少当前原生无有效周期的诊断')
    repeats=groups[0].get('repeats') or []
    require(2<=len(repeats)<=6 and all(r.get('fit') is None
        and CYCLE_REASON in (r.get('diagnostics') or {}).get('rejection_reasons',[]) for r in repeats),
        '原生诊断没有各组无有效周期的依据；不能据其它失败假定慢 Rabi')
    parameters,versions=c._parameters(rt,wf,node)
    actual=result.get('parameters') or {}
    require(all(k in actual and c._same_parameter(value,actual[k]) for k,value in parameters.items()
        if k not in IGNORED),'幅度轴、采样或工作点已改变，须先取得当前设置的实际响应')
    return {'failure':failed,'f01':_f01(c,rt,wf,node,parameters,versions),
        'parameterVersionsSha256':digest(versions),'nativeRejection':CYCLE_REASON,
        'interpretation':'各组拟合被原生诊断拒绝；不是已测得的周期或慢 Rabi 结论。'}

def model_scope(c,wf,node,bounds):
    bounds=deepcopy(bounds)
    if not smart_sampling.eligible(wf,node):return bounds,None
    try:
        with c.store.connect() as rt:
            current=c.get(wf['id'],rt)
            require(current['revision']==wf['revision'],'模型输入版本已改变')
            proof=_proof(c,rt,current,c._node(current,node['id']))
            current_parameters,_=c._parameters(rt,current,c._node(current,node['id']))
        physical=c._parameter_schema(node['procedureId'])['drive_length_s']
        width=proof['failure']['workpoint']['drive_length_s']
        lower=(round(width*2e9)+1)/2e9
        if lower>physical['max']:return bounds,None
        # A current-width echo is not a new-window request. Actual changes
        # still have to extend the window on the native grid in prepare_retry.
        bounds['drive_length_s']={**physical,'min':max(physical['min'],width)}
        return bounds,{'schema':SCHEMA,'isCalibration':False,'currentDurationS':width,
            'proposalPolicy':PROPOSAL_POLICY,'currentParameters':current_parameters,
            'sampleQuantumS':.5e-9,'parameter':'drive_length_s','sourceAttemptId':proof['failure']['sourceAttemptId'],
            'sourceProofSha256':digest(proof),'f01AttemptId':proof['f01']['attemptId'],
            'meaning':'可通过现有 retry 工具探索更长的固定 DC 门；仅改 drive_length_s，若当前16块需同时显式改 acquisitions_per_point=8。'
                '保持幅度轴、驱动频率、读出与功率。新时窗先用原8块版本独立采集，之后16块必须依据新时窗自己的8块实测。'
                '不能从无效拟合推算周期，不承诺出现振荡；质量门槛、首复独立性和计量不变。'}
    except (APIError,OSError,ValueError,KeyError,TypeError):return bounds,None

def prepare_retry(c,rt,wf,node,trial,versions,decision):
    requested=decision.get('parameterChanges') or {}
    if 'drive_length_s' not in requested:return None
    effective,_=c._parameters(rt,wf,node)
    raw=requested['drive_length_s'];raw=raw.get('to') if isinstance(raw,dict) else raw
    if (c._same_parameter(raw,effective.get('drive_length_s'))
            and c._same_parameter(trial.get('drive_length_s'),effective.get('drive_length_s'))):return None
    proof=_proof(c,rt,wf,node)
    actual=node['result']['parameters'];old=actual['drive_length_s'];new=trial.get('drive_length_s')
    require(number(new) and number(raw) and math.isclose(raw,new,rel_tol=0,abs_tol=1e-18)
        and math.isclose(new*2e9,round(new*2e9),rel_tol=0,abs_tol=1e-7),
        '固定门长须明确对齐0.5ns网格，不把舍入后的时长当请求原值')
    rule=c._parameter_schema(node['procedureId'])['drive_length_s']
    require(rule['min']<=new<=rule['max'] and new>old,'本探索仅在既有原生范围内延长固定门长')
    # Models may echo unchanged values. Judge actual parameter differences,
    # while retaining the full original proposal in the authorization digest.
    unpacked={k:v.get('to') if isinstance(v,dict) else v for k,v in requested.items()}
    changed={k for k,v in unpacked.items() if k not in effective or not c._same_parameter(v,effective[k])}
    allowed={'drive_length_s','acquisitions_per_point'}
    require(changed<=allowed and trial.get('acquisitions_per_point')==8
        and (effective.get('acquisitions_per_point')==8 or unpacked.get('acquisitions_per_point')==8),
        '改变门长仅可同时显式恢复8块，不能沿用旧工况16块或改变其它参数')
    require(all(k in effective and k in trial and c._same_parameter(effective[k],trial[k])
        for k in (set(effective)|set(trial))-allowed),'时窗探索不能改变幅度轴、功率或其它工作点')
    auth={'schema':SCHEMA,'id':ident('rabi-window'),'workflowId':wf['id'],'nodeId':node['id'],
        'resourceId':'Q11','procedureId':'sq_rabi_amplitude','adapterId':wf['adapterId'],
        'adapterProfileHash':wf['adapterProfileHash'],'smartRunSha256':digest(smart_sampling.run_identity(wf)),
        'attemptNumber':node['attempt']+1,'sourceAttemptId':proof['failure']['sourceAttemptId'],
        'sourceProof':proof,'sourceProofSha256':digest(proof),'parametersSha256':_context(trial),
        'selectionParametersSha256':digest(trial),
        'parameterVersionsSha256':digest(versions),'fromDurationS':old,'toDurationS':new,
        'decisionSha256':digest(decision),'createdAt':now()}
    return {'authorization':auth,'sha256':digest(auth),'state':'selected'}

def commit_retry(node,record):
    if record is None:return
    if node.get('smartRabiWindow'):node.setdefault('smartRabiWindowHistory',[]).append(deepcopy(node['smartRabiWindow']))
    node['smartRabiWindow']=deepcopy(record)

def _revalidate(c,rt,wf,node,record,parameters,versions,*,verification=False):
    from .gate_calibration_context import _operation
    auth=record.get('authorization') or {}
    require(record.get('sha256')==digest(auth) and auth.get('schema')==SCHEMA
        and auth.get('workflowId')==wf['id'] and auth.get('nodeId')==node['id']
        and auth.get('adapterId')==wf.get('adapterId') and auth.get('adapterProfileHash')==wf.get('adapterProfileHash')
        and smart_sampling.eligible(wf,node) and not wf.get('cancelRequested') and not wf.get('sharedDeviceFault')
        and auth.get('smartRunSha256')==digest(smart_sampling.run_identity(wf)),'时窗探索授权或父任务改变')
    require(auth.get('parametersSha256')==_context(parameters) and auth.get('parameterVersionsSha256')==digest(versions),
        '时窗探索后参数或上游版本改变，请重新按当前实测规划')
    if not verification:
        require(auth.get('selectionParametersSha256')==digest(parameters),'首测完整参数与冻结的时窗探索授权不同')
    proof=auth['sourceProof'];require(auth['sourceProofSha256']==digest(proof),'时窗探索来源摘要改变')
    failure=proof['failure']
    row=rt.db.execute('SELECT payload FROM platform_attempts WHERE workflow_id=? AND node_id=? AND id=?',
        (wf['id'],node['id'],failure['sourceAttemptId'])).fetchone()
    require(row is not None and digest(json.loads(row[0]))==failure['sourceAttemptSha256'],'原失败尝试改变')
    prior=json.loads(row[0]);shadow={**node,'attempt':prior['number'],'latestAttemptId':prior['id'],'result':prior['result']}
    for item in failure['operations']:
        native,raw=_operation(c,rt,wf,shadow,prior,item['operationId'],'时窗来源')
        require(digest(native)==item['resultSha256'] and sorted(raw)==item['rawSha256'],'原失败回执或原始文件改变')
    require(_f01(c,rt,wf,node,parameters,versions)==proof['f01'],'定频或对应原始证据已改变')
    return auth

def check_plan(c,rt,wf,node,parameters,versions):
    record=node.get('smartRabiWindow') or {}
    if record.get('state')!='selected':return
    auth=_revalidate(c,rt,wf,node,record,parameters,versions)
    require(auth['attemptNumber']==node['attempt']+1 and auth['sourceAttemptId']==node.get('latestAttemptId')
        and not node.get('result',{}).get('stale') and node.get('engineState')!='stale',
        '时窗来源或已选计划已失效，不能沿用旧授权下发')

def claim(wf,node,attempt,request):
    record=node.get('smartRabiWindow') or {}
    if record.get('state')!='selected':return
    auth=record['authorization']
    require(auth['attemptNumber']==attempt['number'] and auth['selectionParametersSha256']==digest(request['parameters']),
        '时窗授权尝试或参数不一致')
    binding={'authorization':deepcopy(auth),'sha256':record['sha256'],'attemptId':attempt['id'],
        'selectionOperationId':attempt['id']+'/measure','verificationOperationId':attempt['id']+'/verify'}
    record.update(state='consumed',attemptId=attempt['id'],consumedAt=now())
    request['rabiWindowAuthorization']=deepcopy(binding);attempt['rabiWindowAuthorization']=deepcopy(binding)

def validate_live(c,rt,request):
    binding=request.get('rabiWindowAuthorization')
    if binding is None:return
    wf=c.get(request['runId'],rt);node=c._node(wf,request['nodeId']);record=node.get('smartRabiWindow') or {}
    require(wf.get('status')=='running' and record.get('state')=='consumed'
        and record.get('attemptId')==request['attemptId']==node.get('latestAttemptId')
        and binding.get('authorization')==record.get('authorization') and binding.get('sha256')==record.get('sha256')
        and binding.get('attemptId')==request['attemptId']
        and binding.get('selectionOperationId')==request['attemptId']+'/measure'
        and binding.get('verificationOperationId')==request['attemptId']+'/verify'
        and request['operationId'] in (binding.get('selectionOperationId'),binding.get('verificationOperationId')),
        '实际下发与时窗尝试绑定不一致')
    row=rt.db.execute("SELECT request FROM platform_effects WHERE workflow_id=? AND attempt_id=? AND operation_id=? AND state='pending'",
        (wf['id'],request['attemptId'],request['operationId'])).fetchone()
    require(row is not None and json.loads(row[0])==request,'下发请求与冻结effect不同')
    _,versions=c._parameters(rt,wf,node)
    verification=request['operationId']==binding['verificationOperationId']
    auth=_revalidate(c,rt,wf,node,record,request['parameters'],versions,verification=verification)
    if verification:
        from .gate_calibration_context import _operation
        from .quality import evaluate
        selected=rt.db.execute("SELECT request FROM platform_effects WHERE workflow_id=? AND attempt_id=? AND operation_id=? AND state='completed'",
            (wf['id'],request['attemptId'],binding['selectionOperationId'])).fetchone()
        require(selected is not None,'复测缺少已完成的本次首测')
        selected=json.loads(selected[0])
        require(selected.get('rabiWindowAuthorization')==binding
            and selected.get('leaseToken')==request.get('leaseToken')
            and digest(selected.get('parameters'))==auth['selectionParametersSha256'],'复测与已授权首测、租约或参数不一致')
        attempt=rt.db.execute('SELECT payload FROM platform_attempts WHERE workflow_id=? AND id=?',(wf['id'],request['attemptId'])).fetchone()
        require(attempt is not None,'缺少本次尝试')
        native,_=_operation(c,rt,wf,node,json.loads(attempt[0]),binding['selectionOperationId'],'时窗本次首测')
        evaluated=evaluate(native,node['procedureId'],'real',wf['gates'],independent=False,rule_version=wf['gatesVersion'])
        require(not [r for r in evaluated['quality']['reasons'] if r!='需要独立采集复测'],'首测尚未满足原有数值门槛')
        candidate=c._validated_candidate(node['procedureId'],evaluated,'selection')
        expected={**deepcopy(selected['parameters']),**candidate}
        expected=c.adapter.build_verification_parameters(node['procedureId'],expected,candidate)
        require(digest(request['parameters'])==digest(expected),'复测参数不是本次原生首测候选及标准复测计划')
