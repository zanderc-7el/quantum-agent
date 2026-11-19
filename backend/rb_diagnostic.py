"""Narrow, read-only proof for one same-run RB diagnostic after failed CPMG.

No scheduling, fitting, hardware, database writes, dependency edits or quality
promotion. The caller independently performs native RB plan/budget admission.
"""
from copy import deepcopy
import json

from .common import APIError, checked_path, digest, file_hash
from .capture_diagnostics import _registered_files
from .prior_failure_context import PROFILE_FILES, CHECKED_JSON_NAMES, audited_bindings, _validate_effect
from .catalog import PARAMETERS
from .quality import evaluate

VERSION = 'rb-after-cpmg-diagnostic/1'
PROFILE = 'arclight-private-f8-q11-v2'
IMPLEMENTATION = 'adapters.arclight.ArcLightAdapter'
ALIASES = {'readout_frequency_hz':'readout_hz','readout_amplitude':'readout_amp',
           'drive_frequency_hz':'drive_hz','drive_amplitude':'drive_amp','pi_duration_s':'drive_length_s'}
CALIBRATION = frozenset(('sq_rabi_amplitude','sq_rabi_time','ro_iq01','ro_optimize'))
# Versioned, exact working-point graph; auxiliary procedures keep their owner.
GRAPH = (
 ('s03_rabi_amplitude','sq_rabi_amplitude',()),
 ('s04_readout_fidelity','ro_iq01',('s03_rabi_amplitude','s03_rabi_amplitude:sq_rabi_time')),
 ('s05_readout_optimization','ro_optimize',('s04_readout_fidelity',)),
 ('s06_t1','sq_t1',('s05_readout_optimization',)),
 ('s07_t2','sq_cpmg',('s05_readout_optimization','s07_t2:sq_echo')),
 ('s08_single_qubit_rb','sq_rb',('s05_readout_optimization','s03_rabi_amplitude:sq_allxy','s06_t1','s07_t2')),
 ('s03_rabi_amplitude:sq_rabi_time','sq_rabi_time',('s03_rabi_amplitude',)),
 ('s03_rabi_amplitude:sq_allxy','sq_allxy',('s05_readout_optimization',)),
 ('s07_t2:sq_ramsey','sq_ramsey',('s06_t1',)),
 ('s07_t2:sq_echo','sq_echo',('s07_t2:sq_ramsey',)),
)


def require(condition, code, message):
    if not condition:
        raise APIError(code, message, 409)


def graph_snapshot(db, workflow):
    nodes = [{k: deepcopy(n.get(k)) for k in ('id','ownerId','procedureId','resourceId','dependsOn')} for n in workflow['nodes']]
    expected_nodes = [{'id':'Q11:'+name,'ownerId':name.split(':')[0], 'procedureId':procedure,
                       'resourceId':'Q11','dependsOn':['Q11:'+d for d in dependencies]}
                      for name,procedure,dependencies in GRAPH]
    require(nodes == expected_nodes, 'RB_DIAGNOSTIC_GRAPH', '原10项节点、方法、归属或依赖已改变')
    steps = []
    for node in nodes:
        row = db.execute('SELECT spec FROM steps WHERE id=?', (workflow['id']+'/'+node['id'],)).fetchone()
        require(row is not None, 'RB_DIAGNOSTIC_GRAPH', '原登记步骤契约缺失')
        spec = json.loads(row['spec'])
        reads,writes=PARAMETERS.get(node['procedureId'],([],[]))
        prefix=workflow['id']+':Q11:'
        require(spec.get('id')==workflow['id']+'/'+node['id']
                and spec.get('reads')==[prefix+ALIASES.get(k,k) for k in reads]
                and spec.get('writes')==[prefix+ALIASES.get(k,k) for k in writes]
                and spec.get('resources')==['CY8_SHARED_BUS1']
                and spec.get('depends_on') == [workflow['id']+'/'+d for d in node['dependsOn']],
                'RB_DIAGNOSTIC_GRAPH', '原图依赖与不可变步骤契约不一致')
        steps.append(spec)
    structural_edges = [{'from':e['from'],'to':e['to']} for e in workflow['edges'] if e.get('kind') != 'backtrack']
    expected = [{'from':d,'to':n['id']} for n in nodes for d in n['dependsOn']]
    require(sorted(structural_edges,key=digest)==sorted(expected,key=digest), 'RB_DIAGNOSTIC_GRAPH', '原图边不完整')
    return {'nodes':nodes, 'structuralEdges':structural_edges, 'registeredSteps':steps}


def _attempt(db, workflow, node):
    row = db.execute('SELECT payload FROM platform_attempts WHERE workflow_id=? AND node_id=? ORDER BY number DESC LIMIT 1',
                     (workflow['id'],node['id'])).fetchone()
    require(row is not None,'RB_DIAGNOSTIC_SOURCE','缺少本任务当前采集记录')
    value = json.loads(row['payload'])
    require(value.get('id')==node.get('latestAttemptId') and value.get('number')==node.get('attempt')
            and value.get('mode')=='real' and value.get('procedureId')==node['procedureId']
            and value.get('result')==node.get('result') and value.get('endedAt') and not value.get('error'),
            'RB_DIAGNOSTIC_SOURCE','当前尝试身份、完成状态或原始结果不一致')
    result=value.get('result') or {}
    require(result.get('dataKind')=='real' and not any(result.get(k) for k in ('partial','stale','verificationPending')),
            'RB_DIAGNOSTIC_SOURCE','仅完整、未失效的新采集可作为诊断来源')
    return value


def _authenticate(db, root, workflow, attempt, binding, *, independent):
    operation=attempt.get('operationId')
    require(operation==attempt['id']+'/measure','RB_DIAGNOSTIC_SOURCE','首测操作身份不一致')
    operations=[operation]
    if attempt.get('verificationOperationId'):
        require(attempt['verificationOperationId']==attempt['id']+'/verify','RB_DIAGNOSTIC_SOURCE','复测操作身份不一致')
        operations.append(attempt['verificationOperationId'])
    require(not independent or len(operations)==2,'RB_DIAGNOSTIC_CALIBRATION','校准缺少独立复测')
    result=attempt['result']
    expected=[result['selectionResult'],result] if len(operations)==2 else [result]
    require(len(operations)==2 or not result.get('selectionResult'),'RB_DIAGNOSTIC_SOURCE','首测/复测来源角色不一致')
    effects=list(db.execute('SELECT * FROM platform_effects WHERE attempt_id=?',(attempt['id'],)))
    require(len(effects)==len(operations),'RB_DIAGNOSTIC_SOURCE','来源执行回执不完整')
    raw_sets, receipts=[],[]
    for operation, original in zip(operations,expected):
        matches=[dict(e) for e in effects if e['operation_id']==operation]
        require(len(matches)==1 and matches[0]['state']=='completed','RB_DIAGNOSTIC_SOURCE','来源操作尚未完整结束')
        effect=matches[0];effect['request']=json.loads(effect['request']);effect['result']=json.loads(effect['result'])
        files=_registered_files(db,root,operation,effect['result'])
        checked=[f for f in files if f['kind']=='raw' or f['name'] in CHECKED_JSON_NAMES]
        raw_sets.append(_validate_effect(effect,checked,workflow,attempt,binding,operation,original))
        if independent:
            require(original.get('analysisQuality',{}).get('accepted') is True
                    and evaluate(original,attempt['procedureId'],'real',workflow['gates'],independent=True)['quality']['status']=='passed',
                    'RB_DIAGNOSTIC_CALIBRATION','校准来源原生分析或冻结指标不通过')
        receipts.append({'operationId':operation,'requestSha256':digest(effect['request']),
                         'resultSha256':digest(effect['result']),
                         'artifacts':[{'name':f['name'],'sha256':f['sha256'],'artifactRef':f['artifactRef']} for f in files]})
    require(len(raw_sets)==1 or raw_sets[0].isdisjoint(raw_sets[1]),'RB_DIAGNOSTIC_SOURCE','独立采集复用了原始数据')
    return {'attemptId':attempt['id'],'attemptReceiptSha256':digest(attempt),'operations':receipts}


def build_proof(root, db, workflow, parameters, versions, *, implementation_id):
    """Must run inside the caller's single SQLite transaction/snapshot."""
    try:
        root=checked_path(root)
        require(implementation_id==IMPLEMENTATION and workflow.get('adapterId')==PROFILE
                and workflow.get('mode')=='real' and workflow.get('coverage')=='working_point'
                and workflow.get('resourceIds')==['Q11'] and len(workflow['nodes'])==10,
                'RB_DIAGNOSTIC_SCOPE','诊断分支仅适用已审计Q11 ArcLight v2的10项工作点任务')
        bindings=audited_bindings(implementation_id,{key:(root/name).read_bytes() for key,(name,_) in PROFILE_FILES.items()},
                                  (root/'adapters/arclight_runtime/source_manifest.json').read_bytes())
        binding=bindings.get(PROFILE)
        require(binding is not None and workflow.get('adapterProfileHash')==binding['fingerprint'],
                'RB_DIAGNOSTIC_SCOPE','执行器冻结绑定不一致')
        for name in binding['manifest']:
            require(file_hash(root/'adapters/arclight_runtime'/name)==binding['manifest'][name],
                    'RB_DIAGNOSTIC_SOURCE','冻结分析源码已变化')
        source=next(n for n in workflow['nodes'] if n['procedureId']=='sq_cpmg')
        target=next(n for n in workflow['nodes'] if n['procedureId']=='sq_rb')
        require(source['id'] in target['dependsOn'],'RB_DIAGNOSTIC_GRAPH','原RB到CPMG依赖缺失')
        for node in workflow['nodes']:
            if node['id'] not in (source['id'],target['id']):
                require(node.get('engineState')=='verified' and node.get('result',{}).get('quality',{}).get('status')=='passed'
                        and not node.get('result',{}).get('stale'),'RB_DIAGNOSTIC_PREREQUISITES','除CPMG外的原流程前置步骤尚未验证')
        source_attempt=_attempt(db,workflow,source)
        require(source_attempt.get('state')=='attention' and source_attempt['result'].get('quality',{}).get('status')=='failed'
                and source_attempt['result']['quality'].get('criteria')==workflow['gates']['sq_cpmg']
                and source_attempt['result']['quality'].get('ruleVersion')==workflow['gatesVersion']
                and evaluate(source_attempt['result'],'sq_cpmg','real',workflow['gates'],independent=True)['quality']['status']=='failed',
                'RB_DIAGNOSTIC_SOURCE','需要当前完整CPMG科学失败；不能以停止、异常或未验证代替')
        marker=workflow.get('readoutRevalidations',{}).get('Q11')
        require(not marker or marker.get('state') not in ('active','fixed_failed'),
                'RB_DIAGNOSTIC_CALIBRATION','读出工作点仍需重建，不能诊断RB')
        physical=set(binding['profile']['resources']['Q11'])-{'q','calibrationStatus'}
        committed={k:v for k,v in binding['profile']['resources']['Q11'].items() if k in physical}
        committed_versions={k:0 for k in physical}
        prefix=workflow['id']+':Q11:'
        ledger={r['name']:{'value':json.loads(r['value']),'revision':r['revision']} for r in
                db.execute('SELECT name,value,revision FROM parameters WHERE name LIKE ?',(prefix+'%',))}
        for key in physical:
            entry=ledger.get(prefix+key)
            if entry:committed[key],committed_versions[key]=entry['value'],entry['revision']
            require(type(versions.get(key)) is int and versions[key]>=0 and versions[key]==committed_versions[key]
                    and parameters.get(key)==committed[key],'RB_DIAGNOSTIC_PARAMETERS','RB请求不等于当前11项物理参数和版本')
            require(source_attempt.get('parameters',{}).get(key)==committed[key]
                    and source_attempt.get('parameterVersions',{}).get(key)==committed_versions[key],
                    'RB_DIAGNOSTIC_PARAMETERS','CPMG失败来源不属于当前11项物理参数版本')
        source_receipt=_authenticate(db,root,workflow,source_attempt,binding,independent=False)
        calibration=[];categories=set()
        for node in workflow['nodes']:
            if node['procedureId'] not in CALIBRATION:continue
            # Older amplitude calibration can legitimately predate the later
            # TimeRabi pi-length commit. It is not a current RB reference.
            if any(node.get('result',{}).get('parameters',{}).get(k)!=committed[k]
                   or node.get('parameterVersions',{}).get(k)!=committed_versions[k] for k in physical):continue
            attempt=_attempt(db,workflow,node)
            require(attempt.get('state')=='verified' and attempt['result']['quality']['status']=='passed'
                    and attempt['result']['quality'].get('criteria')==workflow['gates'][node['procedureId']]
                    and attempt['result']['quality'].get('ruleVersion')==workflow['gatesVersion'],
                    'RB_DIAGNOSTIC_CALIBRATION','当前校准并未完成冻结规则验收')
            outputs={ALIASES.get(k,k) for k in PARAMETERS[node['procedureId']][1]}
            candidate={ALIASES.get(k,k):v for k,v in attempt['result'].get('candidateParameters',{}).items()}
            for key in physical:
                old=attempt.get('parameterVersions',{}).get(key)
                require(type(old) is int and (old==versions[key] or
                    key in outputs and old+1==versions[key] and candidate.get(key)==committed[key]),
                    'RB_DIAGNOSTIC_CALIBRATION','校准采集版本与已提交版本无可核验的关系')
            proof=_authenticate(db,root,workflow,attempt,binding,independent=True)
            category='rabi' if node['procedureId'].startswith('sq_rabi') else 'readout'
            categories.add(category)
            calibration.append({'nodeId':node['id'],'procedureId':node['procedureId'],'category':category,**proof})
        require(categories=={'rabi','readout'},'RB_DIAGNOSTIC_CALIBRATION','缺少同任务当前Rabi与读出的独立验证来源')
        result={'policyVersion':VERSION,'workflowId':workflow['id'],'adapterId':PROFILE,
                'adapterProfileHash':binding['fingerprint'],'sourceNodeId':source['id'],'sourceAttemptId':source_attempt['id'],
                'targetNodeId':target['id'],'sourceReceipt':source_receipt,'calibrationReceipts':calibration,
                'physicalParameters':committed,'physicalParameterVersions':committed_versions,
                'parameterLedgerSha256':digest(ledger),'graphSha256':digest(graph_snapshot(db,workflow)),
                'gatesSha256':digest({'gates':workflow['gates'],'gatesVersion':workflow['gatesVersion'],
                                      'supplementalAcceptance':workflow.get('supplementalAcceptance')}),
                'configuredParametersSha256':digest({n['id']:n.get('parameterOverrides',{}) for n in workflow['nodes']})}
        result['sourceProofSha256']=digest(result)
        return result
    except APIError:
        raise
    except (OSError,ValueError,KeyError,TypeError,IndexError,AttributeError,StopIteration):
        raise APIError('RB_DIAGNOSTIC_SOURCE','诊断来源、原始产物或版本证据不可核验',409) from None
