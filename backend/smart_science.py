"""Current-source, single-step science children of one explicitly authorized Q11 run.

Only normal Controller/ScienceService contracts create, plan, acquire, accept and
archive. No waveform, quality rule, native whitelist or hardware driver changes.
"""
from copy import deepcopy
from contextlib import closing
import json
import sqlite3
from pathlib import Path
from subprocess import TimeoutExpired

from .common import APIError, digest, dumps, now, number

SCHEMA='qcal-smart-science-child/1'
PARENT_SCHEMA='q11-full-same-gate/1'
PROFILE='science-shape-repeat5'
METHODS=('sq_shape_rabi_amplitude_repeat5','sq_shape_half_verify','sq_drag_half_scan','sq_drag_irb')
LABELS=('五门整形幅度 Rabi','同形状半门验证','同半门经验 DRAG','同半门交错 RB')
CONTEXT=('drive_hz','drive_amp','drive_length_s','readout_hz','readout_amp','readout_width_s',
         'xy_delay_s','xy_attenuation_db','xy_filter_mhz')
TARGET=('pulse_shape','pulse_length_s','pulse_sigma_s','pulse_phase_rad','shape_pi_amp')
SELECTED_OUTPUT={METHODS[0]:'shape_pi_amp',METHODS[2]:'drag_beta_s'}
ALLOWED_RETRY={
    METHODS[0]:{'pulse_shape','pulse_sigma_s','amp_start','amp_stop','points','shots','capture_repeats','acquisitions_per_point','order_seed'},
    METHODS[1]:{'shots','capture_repeats','acquisitions_per_point','order_seed'},
    METHODS[2]:{'beta_start_s','beta_stop_s','points','drag_repeats','shots','capture_repeats','acquisitions_per_point','order_seed'},
    METHODS[3]:{'rb_lengths','rb_sequences','rb_captures','reference_captures','rb_master_seed','rb_bootstrap_replicates','shots','acquisitions_per_point'},
}


def require(ok,message,code='SMART_SCIENCE_SCOPE'):
    if not ok:raise APIError(code,message,409)


def main_context(parent):
    require(len(parent.get('nodes',[]))==13 and all(n.get('engineState')=='verified'
        and n.get('result',{}).get('dataKind')=='real' and n.get('result',{}).get('quality',{}).get('status')=='passed'
        and not any(n.get('result',{}).get(k) for k in ('partial','stale','verificationPending'))
        and n.get('latestAttemptId') for n in parent['nodes']), '主流程尚未完成当前工况的独立验收','SMART_MAIN_REQUIRED')
    return digest([{'id':n['id'],'attemptId':n['latestAttemptId'],'resultSha256':digest(n['result'])} for n in parent['nodes']])


def parent_live(parent):
    return (parent.get('mode')=='real' and parent.get('policy')=='autopilot'
        and parent.get('resourceIds')==['Q11'] and parent.get('adapterId')=='arclight-private-f8-q11-v3'
        and parent.get('smartRun',{}).get('schema')==PARENT_SCHEMA
        and parent['smartRun'].get('adapterProfileHash')==parent.get('adapterProfileHash')
        and parent.get('hardwareConfirmed') is True and (parent.get('budget') or {}).get('mode')=='accounting_only'
        and parent.get('status') in ('running','queued') and not parent.get('cancelRequested'))


def assert_authorized(controller,child,parent_id=None,rt=None):
    if rt is None:
        with controller.store.connect() as reader:return assert_authorized(controller,child,parent_id,reader)
    binding=child.get('smartParent') or {};parent_id=parent_id or binding.get('workflowId')
    parent=controller.store.get(rt,parent_id) if isinstance(parent_id,str) else None
    require(parent is not None and parent_live(parent),'父智能任务已暂停、结束或缺少当次授权')
    index=binding.get('index');method=binding.get('procedureId')
    require(type(index) is int and 0<=index<len(METHODS) and method==METHODS[index]
        and binding.get('schema')==SCHEMA and binding.get('workflowId')==parent['id']
        and binding.get('parentAdapterProfileHash')==parent['adapterProfileHash']
        and parent['smartRun'].get('children',{}).get(method)==child['id']
        and child.get('policy')=='strict' and child.get('mode')=='real' and child.get('resourceIds')==['Q11']
        and child.get('executionProfile')==PROFILE and len(child.get('nodes',[]))==1
        and child['nodes'][0]['procedureId']==method,'科学子任务与授权父任务或步骤不匹配')
    observed=child.get('observedResourceBinding') or {};budget=child.get('budget',{}).get('parent',{})
    require(observed.get('sourceWorkflowId')==parent['id'] and observed.get('resourceId')=='Q11'
        and observed.get('sourceAdapterProfileHash')==parent['adapterProfileHash']
        and budget=={'parentWorkflowId':parent['id'],'parentSchema':PARENT_SCHEMA,'sourceAdapterProfileHash':parent['adapterProfileHash']}
        and binding.get('mainContextSha256')==main_context(parent),'父标定、配置来源或计量关系已变化','SMART_SCIENCE_SOURCE_CHANGED')
    controller.for_workflow(parent['id'],rt)._assert_adapter_binding(parent)
    controller.for_workflow(child['id'],rt)._assert_adapter_binding(child)
    return parent


def cancelled(controller,child):
    if not child.get('smartParent'):return False
    binding=child['smartParent']
    try:
        with closing(sqlite3.connect(controller.store.path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            row=db.execute('''SELECT revision,json_extract(payload,'$.status'),
                json_extract(payload,'$.cancelRequested') FROM platform_workflows WHERE id=?''',
                (binding.get('workflowId'),)).fetchone()
        if row is None or row[1] not in ('running','queued') or row[2]:return True
        key=(child['id'],digest(binding),row[0])
        if getattr(controller,'_smart_cancel_checkpoint',None)!=key:
            assert_authorized(controller,child)
            controller._smart_cancel_checkpoint=key
    except (APIError,sqlite3.Error,OSError):return True
    return False


def validate_target(controller,rt,child,parameters):
    if not child.get('smartParent'):return
    assert_authorized(controller,child,rt=rt)
    frozen=child['smartParent'].get('frozenParameters')
    require(isinstance(frozen,dict) and all(parameters.get(k)==v for k,v in frozen.items()),
        '同门或当前工作点参数已改变，请先重新完成相应来源标定','SMART_SCIENCE_TARGET_CHANGED')


def decision_bounds(child,bounds):
    if not child.get('smartParent'):return bounds
    method=child['smartParent']['procedureId']
    return {k:deepcopy(v) for k,v in bounds.items() if k in ALLOWED_RETRY[method]}


def validate_decision(controller,child,node,proposal,bounds):
    """Retain ordinary proposal/evidence validation; use the existing array
    schema validator for scientific RB's explicit length grid."""
    from .quality import validate_proposal
    trimmed=deepcopy(proposal)
    arrays={k:v for k,v in (proposal.get('parameterChanges',{}) if isinstance(proposal,dict)
            and isinstance(proposal.get('parameterChanges',{}),dict) else {}).items()
            if bounds.get(k,{}).get('type') in ('integerArray','numberArray')}
    if arrays:
        for k in arrays:trimmed['parameterChanges'].pop(k)
    validate_proposal(trimmed,child,node,bounds)
    for key,value in arrays.items():
        controller._validate_parameter(key,value.get('to') if isinstance(value,dict) else value,bounds)
    return deepcopy(proposal)


def initial_parameters(parameters,timing):
    continuous=timing.get('selectionResult',{}).get('candidateParameters',{}).get('drive_length_s')
    require(number(continuous) and 4e-9<=continuous<=2e-6,'时间 Rabi 缺少连续拟合时长','SMART_TIME_SOURCE_REQUIRED')
    duration=round(continuous*4e9)/4e9
    return {**{k:deepcopy(parameters[k]) for k in CONTEXT if k in parameters},
        'pulse_shape':'Gaussian','pulse_length_s':duration,'pulse_sigma_s':duration/2,
        'pulse_phase_rad':0.,'shape_pi_amp':.3,'shots':2000,'acquisitions_per_point':4,
        'capture_repeats':4,'points':41,'amp_start':.02,'amp_stop':.98,'order_seed':20261127}


def followup_parameters(index,record):
    result=record.get('result',{});source=result.get('parameters',{})
    required=[k for k in CONTEXT+TARGET if not (index==3 and k in ('drive_amp','drive_length_s'))
              and (k!='pulse_sigma_s' or source.get('pulse_shape')=='Gaussian')]
    require(all(k in source for k in required),
        '整形来源缺少完整门及读出参数','SMART_SHAPE_SOURCE_REQUIRED')
    p={k:deepcopy(source[k]) for k in CONTEXT+TARGET if k in source}
    p.update(shots=2000,acquisitions_per_point=4,capture_repeats=4,order_seed=20261127)
    if index in (1,2):
        nodes=record.get('dependencyProof',{}).get('nodes',[])
        own=next((n for n in nodes if n.get('recordId')==record.get('id')),None)
        # The record's self acquisition is resolved by the caller and supplied
        # as sourceCandidate. A proof entry is useful for isolated contract tests.
        candidate=record.get('sourceCandidate') or (own or {}).get('resolvedOutputs',{}).get('candidateParameters',{})
        require(number(candidate.get('shape_pi_amp')),'缺少已独立验证的单门 π 幅度','SMART_SHAPE_SOURCE_REQUIRED')
        p['shape_pi_amp']=candidate['shape_pi_amp']
    if index==2:p.update(drag_mode='empirical_beta',drag_beta_s=0.,beta_start_s=-1e-9,beta_stop_s=1e-9,points=21,drag_repeats=4)
    if index==3:
        identity=result.get('targetPulseIdentity',{}).get('identity',{})
        require(identity.get('dragMode')=='empirical_beta' and number(identity.get('betaSeconds')),
            'IRB 缺少同半门的实测经验系数身份','SMART_DRAG_SOURCE_REQUIRED')
        p.update(drag_mode='empirical_beta',drag_beta_s=identity['betaSeconds'],rb_lengths=[1,2,4,8,16,32],
            rb_sequences=20,rb_captures=2,reference_captures=4,rb_master_seed=20261127,
            rb_bootstrap_replicates=1000,rb_target='X/2')
        p.pop('capture_repeats');p.pop('order_seed')
        # IRB's target must inherit the full-pi preparation amplitude; the
        # targetPulseIdentity amplitude itself describes X/2.
        p.pop('drive_amp',None);p.pop('drive_length_s',None)
    return p


def references(index,sources,records):
    if index==0:pairs=[('f01',sources['f01']),('readout',sources['readout'])]
    elif index==1:pairs=[('pi01',records[0]),('readout',sources['readout'])]
    elif index==2:pairs=[('pi01',records[0]),('half01',records[1]),('rabi_amplitude',records[0]),('rabi_time',sources['rabi_time']),('readout',sources['readout'])]
    else:pairs=[('pi01',records[0]),('half01',records[1]),('drag',records[2]),('readout',sources['readout'])]
    return [{'role':role,'recordId':record} for role,record in pairs]


def bounded_irb_parameters(parameters,planner):
    """Native planning only. Shorten the initial grid solely for its actual
    fixed control window, and validate both independent native seed sets."""
    from adapters.science_v3_irb.plans import verification_parameters
    attempts=[]
    for axis in (parameters['rb_lengths'],[1,2,4,8,16],[1,2,3,4,8],[1,2,3,4,5]):
        if axis in attempts:continue
        attempts.append(axis)
        trial={**deepcopy(parameters),'rb_lengths':deepcopy(axis)}
        try:
            captures=[]
            for stage,p in (('selection',trial),('verification',verification_parameters(trial))):
                campaign=planner(p)
                captures.append({'stage':stage,'seed':p['rb_master_seed'],'campaignSha256':campaign['sha256'],
                    'budget':deepcopy(campaign['budget']),
                    'maximumControlDurationS':max((sum(g['pulse']['durationNs']+2 for g in row['gates'])*1e-9
                        for row in campaign['rows']),default=0.)})
            return trial,{'policy':'initial_native_irb_window_plan/1','nativePlanningOnly':True,
                'hardwareDispatched':False,'triedLengthGrids':deepcopy(attempts),'captures':captures}
        except ValueError as exc:
            if 'sequence exceeds fixed control window' not in str(exc):
                raise APIError('SMART_IRB_PLAN_REJECTED',str(exc),409) from None
        except (OSError,TimeoutExpired) as exc:
            raise APIError('SMART_IRB_PLANNER_UNAVAILABLE','只读原生规划未完成：'+type(exc).__name__,409) from None
    raise APIError('SMART_IRB_PLAN_REJECTED','同门最短合法 IRB 长度仍超出固定控制窗口',409)


def native_initial_planner(root,python,parameters):
    """The existing audited read-only plan worker with explicit UTF-8 stdio.
    -I ignores inherited PYTHON* settings, so select the mode in argv rather
    than mutating process/global environment or any frozen native source."""
    import subprocess
    from adapters.science_v3_irb.policy import verify_sources
    from adapters.science_v3_irb.plans import normalize_parameters
    root=Path(root);verify_sources(root);p,_=normalize_parameters(parameters)
    body=json.dumps(p,allow_nan=False).encode('utf8')
    require(len(body)<=65536,'IRB 规划输入超出有界容量','SMART_IRB_PLAN_REJECTED')
    result=subprocess.run([str(python),'-I','-B','-X','utf8',str(root/'adapters/science_v3_irb/plan_worker.py')],
        input=body,capture_output=True,timeout=45,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:
        raise ValueError('Native IRB planning failed: '+result.stderr[-16000:].decode('utf8',errors='replace'))
    if len(result.stdout)>64_000_000:raise ValueError('Native IRB plan exceeds output budget')
    return json.loads(result.stdout.decode('utf8'))


def reportable(record):
    r=record.get('result',{});g=r.get('gateCharacterization',{});f=r.get('metrics',{}).get('gate_fidelity')
    return (record.get('kind')=='measurement' and record.get('dataKind')=='real'
        and record.get('evidenceCurrent') is True and record.get('evidenceIntact') is True
        and record.get('provenance',{}).get('independentlyVerified') is True
        and r.get('procedureId')==METHODS[-1] and r.get('quality',{}).get('status')=='passed'
        and not r.get('partial') and g.get('reportable') is True and g.get('irb',{}).get('reportable') is True
        and number(f) and 0<=f<=1 and bool(r.get('targetPulseIdentity',{}).get('sha256')))


def _archive(controller,wf,node):
    from .science_service import ScienceService
    require(node.get('engineState')=='verified' and node.get('latestAttemptId')
        and node.get('result',{}).get('quality',{}).get('status')=='passed','来源尚未独立验收','SMART_SOURCE_REQUIRED')
    body={'operationId':'smart-science-source/'+digest({'wf':wf['id'],'attempt':node['latestAttemptId']}),
        'kind':'measurement','input':{},'scope':{},'reference':{'workflowId':wf['id'],'attemptId':node['latestAttemptId']},
        'label':node['label']+' · 智能任务采集'}
    service=ScienceService(controller);record=service.write('/records',body,'POST')
    # An idempotent old response is not sufficient for current qualification.
    with controller.store.connect() as rt:record=service._load(rt,record['id'])
    require(record.get('evidenceCurrent') is True and record.get('evidenceIntact') is True
        and record.get('provenance',{}).get('independentlyVerified') is True,
        '已归档来源不再是当前独立验收结果','SMART_SOURCE_REQUIRED')
    return record


def _issue(controller,parent_id,exc,child_id=None):
    with controller.store.transaction() as rt:
        parent=controller.get(parent_id,rt)
        if not parent_live(parent):return
        issue={'code':getattr(exc,'code','SMART_SCIENCE_FAILED'),'message':str(exc)[:1200],'childWorkflowId':child_id,'at':now()}
        parent['smartRun'].update(gate='attention',scienceIssue=issue)
        parent.update(status='attention',engineState='science_attention')
        controller.store.save(rt,parent,'science_attention',{'message':issue['message']})


def begin(controller,parent_id):
    """Called instead of ordinary _complete after the thirteen main nodes."""
    with controller.store.transaction() as rt:
        parent=controller.get(parent_id,rt)
        if parent.get('smartRun',{}).get('schema')!=PARENT_SCHEMA:return False
        require(parent_live(parent),'智能父任务当前没有继续执行许可')
        context=main_context(parent);smart=parent['smartRun']
        if smart.get('mainContextSha256') not in (None,context):
            for wid in smart.get('children',{}).values():
                child=controller.get(wid,rt)
                require(child['status'] not in ('queued','running','stopping')
                    and not controller._has_lease(rt,wid)
                    and not rt.db.execute("SELECT 1 FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')",(wid,)).fetchone(),
                    '旧科学子任务尚未停止或仍有采集回执待核对')
            smart.setdefault('childrenHistory',[]).extend({'procedureId':method,'workflowId':wid,
                'mainContextSha256':smart.get('mainContextSha256'),'supersededAt':now()} for method,wid in smart.get('children',{}).items())
            for key in ('children','scienceRecords','sourceRecords','gateEvidence','scienceIssue'):smart.pop(key,None)
            smart.update(gate='pending',report='pending')
        smart.update(main='verified',mainContextSha256=context)
        parent.update(status='running',engineState='science_running',workflowComplete=False)
        controller.store.save(rt,parent,'science_chain_ready',{'message':'主流程已独立验收，继续同门标定与交错 RB'})
    controller.wake.set();return True


def recover_parent(controller,rt,parent):
    """A coordinator has no hardware effect of its own. Preserve only a fully
    persisted clean chain; unknown child acquisition still uses reconciliation."""
    if (not parent_live(parent) or parent.get('engineState') not in ('science_running','report_pending')
            or parent['smartRun'].get('main')!='verified'):return False
    try:
        require(parent['smartRun'].get('mainContextSha256')==main_context(parent),'主流程版本改变')
        controller.for_workflow(parent['id'],rt)._assert_adapter_binding(parent)
        children=parent['smartRun'].get('children',{})
        require(isinstance(children,dict) and set(children)==set(METHODS[:len(children)]),'子流程顺序不完整')
        for identifier in [parent['id'],*children.values()]:
            require(not controller._has_lease(rt,identifier) and not rt.db.execute(
                "SELECT 1 FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')",(identifier,)).fetchone(),'尚有未决采集')
        for node in parent['nodes']:
            row=rt.db.execute('SELECT payload FROM platform_attempts WHERE workflow_id=? AND id=? AND node_id=?',
                (parent['id'],node['latestAttemptId'],node['id'])).fetchone()
            attempt=json.loads(row[0]) if row else {}
            require(attempt.get('state')=='verified' and attempt.get('result')==node['result']
                and attempt.get('operationId') and attempt.get('verificationOperationId')!=attempt.get('operationId'),'主流程尝试不完整')
            for operation in (attempt.get('operationId'),attempt.get('verificationOperationId')):
                effect=rt.db.execute('SELECT state FROM platform_effects WHERE workflow_id=? AND attempt_id=? AND operation_id=?',
                    (parent['id'],attempt['id'],operation)).fetchone()
                require(effect is not None and effect[0]=='completed','主流程回执不完整')
        for method,identifier in children.items():
            child=controller.get(identifier,rt);assert_authorized(controller,child,parent['id'],rt)
        if parent['engineState']=='report_pending':
            require(parent['smartRun'].get('gate')=='verified' and len(children)==4
                and set(parent['smartRun'].get('scienceRecords',{}))==set(METHODS),'同门证据不完整')
        return True
    except APIError:return False


def _create_child(controller,parent,index):
    source_nodes={role:next((n for n in parent['nodes'] if n['procedureId']==method),None)
        for role,method in (('f01','sq_spectroscopy'),('rabi_time','sq_rabi_time'),('readout','ro_optimize'))}
    require(all(source_nodes.values()),'主流程缺少定频、时间 Rabi 或读出来源')
    records={role:_archive(controller,parent,node) for role,node in source_nodes.items()}
    source_ids={role:r['id'] for role,r in records.items()}
    prior_ids=[parent['smartRun'].get('scienceRecords',{}).get(method) for method in METHODS[:index]]
    require(all(prior_ids),'前一科学子项尚未保存独立结果')
    if index==0:
        with controller.store.connect() as rt:
            values,_=controller._parameters(rt,parent,{**source_nodes['rabi_time'],'parameterOverrides':{}})
        values=initial_parameters(values,source_nodes['rabi_time']['result'])
    else:
        from .science_service import ScienceService
        source_id=prior_ids[2] if index==3 else prior_ids[0]
        with controller.store.connect() as rt:source=ScienceService(controller)._load(rt,source_id)
        source_child=controller.get(parent['smartRun']['children'][METHODS[2 if index==3 else 0]])
        source['sourceCandidate']=deepcopy(source_child['nodes'][0]['result'].get('candidateParameters',{}))
        values=followup_parameters(index,source)
    # Use the exact ordinary creation schema, including mode-specific fields.
    # Legacy context metadata such as drive_amp is not an editable beta field.
    catalog=controller.science_catalog(resource_id='Q11',source_workflow_id=parent['id'],execution_profile=PROFILE)
    method_schema=next(m for m in catalog['manualMethods'] if m['procedureId']==METHODS[index])
    keys=set(method_schema['parameterBounds'])
    mode_keys=method_schema.get('parameterKeysByMode',{}).get(values.get('drag_mode','normalized_alpha'))
    if mode_keys is not None:keys.intersection_update(mode_keys)
    values={k:v for k,v in values.items() if k in keys}
    initial_plan=None
    if index==3:
        base=controller.for_workflow(parent['id'])
        values,initial_plan=bounded_irb_parameters(values,lambda p:native_initial_planner(controller.root,base.profile['python'],p))
    with controller.store.transaction() as rt:
        current=controller.get(parent['id'],rt)
        require(parent_live(current) and main_context(current)==main_context(parent),'父任务或主流程来源已改变')
        smart=current['smartRun'];method=METHODS[index]
        if method in smart.get('children',{}):return smart['children'][method]
        child=controller.create({'name':parent['name']+' · '+LABELS[index],'mode':'real','policy':'strict',
            'experiment':'single','resourceIds':['Q11'],'executionProfile':PROFILE,'sourceWorkflowId':parent['id'],
            'manualProcedureId':method,'manualParameters':values,'manualScienceReferences':references(index,source_ids,prior_ids)},
            _rt=rt,_budget_parent=parent['id'])
        node=child['nodes'][0]
        # A scan's own selected output is frozen by Controller's independent
        # verification request, not by its initial numeric placeholder. The
        # preceding calibrated pulse/readout fields remain exact constraints.
        frozen={k:deepcopy(v) for k,v in node['parameterOverrides'].items()
                if k not in ALLOWED_RETRY[method] and k!=SELECTED_OUTPUT.get(method)}
        child['smartParent']={'schema':SCHEMA,'workflowId':parent['id'],'procedureId':method,'index':index,
            'parentAdapterProfileHash':parent['adapterProfileHash'],'mainContextSha256':main_context(parent),
            'frozenParameters':frozen,'createdAt':now()}
        if initial_plan:child['smartParent']['initialPlan']=initial_plan
        child['completionScope']['meaning']='仅本科学测项首测及独立复测通过，由已授权智能父任务验收'
        smart.setdefault('children',{})[method]=child['id']
        smart.update(sourceRecords=source_ids,scienceIndex=index,gate='running',childrenOrder=list(METHODS[:index+1]))
        controller.store.save(rt,child,'smart_parent_bound',{'message':'已绑定智能父任务；尚未发出单步采集许可'})
        controller.store.save(rt,current,'science_child_created',{'message':'已创建 '+LABELS[index],'childWorkflowId':child['id']})
        return child['id']


def _accept(controller,child):
    with controller.store.transaction() as rt:
        current=controller.get(child['id'],rt);parent=assert_authorized(controller,current,rt=rt)
        node=current['nodes'][0]
        if node['engineState']=='verified':return current
        scoped=controller.for_workflow(current['id'],rt)
        scoped._approve_manual_step(rt,current,{'nodeId':node['id'],'attemptId':node['latestAttemptId']},_smart_parent=parent['id'])
        controller.store.save(rt,current,'smart_science_accepted',{'message':'智能父任务确认本步独立复测及当前来源','attemptId':node['latestAttemptId']})
        return current


def _terminal_decision(child):
    if child.get('engineState')=='waiting_operator':
        return 'SMART_CHILD_WAITING'
    if (child.get('engineState')=='waiting_model'
            and child.get('modelRecovery',{}).get('state')=='blocked'):
        return 'SMART_CHILD_MODEL_BLOCKED'
    return None


def _decision_attention(child):
    """A resolved wait or blocked model must not look like active acquisition."""
    code=_terminal_decision(child)
    if code:
        raise APIError(code,child['nodes'][0].get('detail') or '科学子项规划已停止，请查看当前实测及模型原因',409)


def advance(controller,parent_id):
    """One bounded orchestration transition; the normal queue owns acquisition."""
    parent=controller.get(parent_id)
    if not parent_live(parent) or parent['smartRun'].get('main')!='verified' or parent['smartRun'].get('gate')=='verified':return
    child_id=None
    try:
        main_context(parent)
        index=next((i for i,m in enumerate(METHODS) if m not in parent['smartRun'].get('scienceRecords',{})),len(METHODS))
        if index==len(METHODS):return
        method=METHODS[index];child_id=parent['smartRun'].get('children',{}).get(method)
        if child_id is None:child_id=_create_child(controller,parent,index)
        child=controller.get(child_id);assert_authorized(controller,child,parent_id)
        scoped=controller.for_workflow(child_id);node=child['nodes'][0]
        stopped=child['smartParent'].get('parentStop')
        if child['status']=='paused' and stopped and stopped.get('action')=='pause':
            with scoped.store.transaction() as rt:
                child=scoped.get(child_id,rt);assert_authorized(scoped,child,parent_id,rt)
                scoped._manual_idle(rt,child)
                child['smartParent'].pop('parentStop',None)
                if child['nodes'][0]['engineState'] not in ('verified','awaiting_acceptance'):
                    child['nodes'][0]['engineState']='retry_queued'
                    child['engineState']='manual_ready'
                scoped.store.save(rt,child,'smart_science_resume_ready',{'message':'父任务已恢复；仅准备新的完整尝试，不续发中断采集'})
                node=child['nodes'][0]
        elif child['status']=='aborted' or child.get('cancelRequested') or (child['status']=='paused' and child['engineState']=='paused'):
            action='abort' if child['status']=='aborted' or child.get('cancelRequested')=='abort' else 'pause'
            current=controller.get(parent_id)
            controller.action(parent_id,{'operationId':'smart-child-stop/'+digest({'child':child_id,'revision':child['revision'],'action':action}),
                'expectedRevision':current['revision'],'action':action})
            return
        retry_ready=(child['status']=='queued' and node['engineState']=='retry_queued'
                     and child.get('manualStep',{}).get('state') not in ('queued','running'))
        if child['status'] in ('queued','running','stopping') and not retry_ready:return
        if child.get('engineState')=='awaiting_acceptance':child=_accept(scoped,child);node=child['nodes'][0]
        if node['engineState']=='verified':
            record=_archive(scoped,child,node)
            if index==3:require(reportable(record),'IRB 尚无可报告的独立同门保真度','SMART_GATE_UNREPORTABLE')
            with controller.store.transaction() as rt:
                current=controller.get(parent_id,rt);assert_authorized(controller,controller.get(child_id,rt),parent_id,rt)
                smart=current['smartRun'];smart.setdefault('scienceRecords',{})[method]=record['id'];smart['scienceIndex']=index+1
                if index==3:
                    smart.update(gate='verified',report='pending',gateEvidence={'recordId':record['id'],
                        'childWorkflowId':child_id,'attemptId':node['latestAttemptId'],'reportable':True,
                        'metrics':deepcopy(record['result']['metrics']),
                        'gateCharacterization':deepcopy(record['result']['gateCharacterization']),
                        'targetPulseIdentity':deepcopy(record['result']['targetPulseIdentity'])})
                    current.update(status='running',engineState='report_pending')
                    from .smart_q11_report_jobs import enqueue
                    enqueue(rt,current)
                controller.store.save(rt,current,'science_child_archived',{'message':LABELS[index]+' 已保存独立采集证据','recordId':record['id']})
            controller.wake.set();return
        require(child['status'] not in ('aborted','failed'),'科学子任务已停止，未自动重建','SMART_CHILD_STOPPED')
        _decision_attention(child)
        if node.get('attempt',0)>0 and node.get('engineState') not in ('retry_queued','stale','pending'):
            # A model may propose bounded changes; it can never approve failed
            # data. Parent authorization is checked again after the model call.
            if child['engineState'] not in ('quality_attention','waiting_model','waiting_operator'):
                raise APIError('SMART_CHILD_BLOCKED',node.get('detail') or '科学子任务需要处理当前回执',409)
            from .model_recovery import child_decision_ready
            if not child_decision_ready(scoped,child,parent_id):return
            scoped._decide(child_id,node['id'],_smart_parent=parent_id)
            child=scoped.get(child_id);assert_authorized(scoped,child,parent_id);node=child['nodes'][0]
            _decision_attention(child)
            if node['engineState']!='retry_queued':return
        with scoped.store.transaction() as rt:
            current=scoped.get(child_id,rt);assert_authorized(scoped,current,parent_id,rt)
            require(not scoped._has_lease(rt,child_id),'科学子任务仍有未结束租约')
            if current['status']=='queued' and current['nodes'][0]['engineState']=='retry_queued':
                current.update(status='paused',engineState='manual_ready')
                scoped.store.save(rt,current,'smart_science_retry_admitted',{'message':'模型方案已通过原计划校验，准备下一次独立尝试'})
            if current['status'] not in ('draft','paused','attention'):return
            payload={'operationId':'smart-science-run/'+digest({'child':child_id,'attempt':current['nodes'][0]['attempt']+1,'revision':current['revision']}),
                'expectedRevision':current['revision'],'action':'run_step','nodeId':current['nodes'][0]['id']}
            scoped.action(child_id,payload,_rt=rt)
    except APIError as exc:_issue(controller,parent_id,exc,child_id)


def synchronize_stop(controller,parent_id):
    parent=controller.get(parent_id)
    if parent_live(parent):return
    if parent.get('smartRun',{}).get('schema')!=PARENT_SCHEMA:return
    action='abort' if parent.get('status') in ('aborted','failed') or parent.get('cancelRequested')=='abort' else 'pause'
    for child_id in parent['smartRun'].get('children',{}).values():
        child=controller.get(child_id)
        if child.get('smartParent',{}).get('workflowId')!=parent_id or child['status'] in ('completed','aborted','failed'):continue
        with controller.store.transaction() as rt:
            child=controller.get(child_id,rt)
            # Preserve an idle resolved model disposition. Turning it into a
            # parentStop pause would let a later generic resume request issue
            # an unchanged acquisition despite that explicit wait/block.
            if (action=='pause' and _terminal_decision(child)
                    and not controller._has_lease(rt,child_id)
                    and not rt.db.execute("SELECT 1 FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')",(child_id,)).fetchone()):
                continue
            if child['status']!='paused' or action!='pause':
                child=controller.action(child_id,{'operationId':'smart-science-stop/'+digest({'parent':parent_id,'revision':parent['revision'],'child':child_id,'action':action}),
                    'expectedRevision':child['revision'],'action':action},_rt=rt)
            marker={'action':action,'parentRevision':parent['revision']}
            if child['smartParent'].get('parentStop')!=marker:
                child['smartParent']['parentStop']=marker
                controller.store.save(rt,child,'smart_parent_stop',{'message':'已跟随智能父任务停止'})
    with controller.store.transaction() as rt:
        current=controller.get(parent_id,rt)
        ids=[parent_id,*current['smartRun'].get('children',{}).values()]
        if current.get('cancelRequested') and not any(controller._has_lease(rt,wid) or rt.db.execute(
                "SELECT 1 FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')",(wid,)).fetchone() for wid in ids):
            controller._finish_cancel_locked(rt,current)
            controller.store.save(rt,current,'smart_parent_stopped',{'message':'父子任务已在清理回执检查点停止'})


def tick(controller):
    # The indexed opt-in accounting table excludes the historical workflow
    # payloads; an ordinary installation does not construct Runtime or decode
    # its old acquisition results at every poll.
    with closing(sqlite3.connect(controller.store.path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        ids=[r[0] for r in db.execute('''SELECT w.id FROM platform_accounting_workflows a
            JOIN platform_workflows w ON w.id=a.workflow_id WHERE a.parent_id IS NULL
            AND json_extract(w.payload,'$.smartRun.schema')=?
            AND json_extract(w.payload,'$.status')<>'completed' ''',(PARENT_SCHEMA,))]
    for identifier in ids:
        parent=controller.get(identifier)
        if parent_live(parent):advance(controller,parent['id'])
        else:synchronize_stop(controller,parent['id'])
