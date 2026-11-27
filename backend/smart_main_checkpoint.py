"""Durable next action only after an opted-in main acquisition is complete.

This never resumes an acquisition or creates a permit. The normal queue repeats
all dispatch checks; a failed result can only enter the model-decision path.
"""
import json
from .common import APIError, digest, now

SCHEMA='qcal-smart-main-checkpoint/1'


def is_smart_main(wf):
    return (wf.get('mode')=='real' and wf.get('policy')=='autopilot'
        and wf.get('coverage')=='full' and wf.get('resourceIds')==['Q11']
        and len(wf.get('nodes',[]))==13 and not wf.get('smartParent')
        and wf.get('adapterId')=='arclight-private-f8-q11-v3'
        and wf.get('smartRun',{}).get('schema')=='q11-full-same-gate/1'
        and wf['smartRun'].get('adapterProfileHash')==wf.get('adapterProfileHash')
        and wf.get('budget',{}).get('mode')=='accounting_only'
        and wf.get('hardwareConfirmed') is True)


def _proof(c,rt,wf,node_id):
    if (not is_smart_main(wf) or wf.get('cancelRequested') or wf.get('sharedDeviceFault')
            or wf['status'] not in ('running','queued') or c._has_lease(rt,wf['id'])
            or rt.db.execute("SELECT 1 FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')",(wf['id'],)).fetchone()
            or any(n.get('engineState') in ('needs_reconciliation','unknown','acquiring','verifying','device_disconnected','ownership_unknown') for n in wf['nodes'])):
        return None
    c._assert_adapter_binding(wf)
    node=next((n for n in wf['nodes'] if n['id']==node_id),None)
    if not node or node.get('engineState') not in ('verified','quality_attention'):return None
    row=rt.db.execute('SELECT payload FROM platform_attempts WHERE workflow_id=? AND node_id=? AND id=?',
        (wf['id'],node['id'],node.get('latestAttemptId'))).fetchone()
    attempt=json.loads(row[0]) if row else {}
    result=attempt.get('result') or {}
    passed=node['engineState']=='verified'
    if (not attempt.get('endedAt') or attempt.get('error') or attempt.get('number')!=node.get('attempt')
            or attempt.get('state')!=('verified' if passed else 'attention')
            or result!=node.get('result') or result.get('dataKind')!='real'
            or result.get('quality',{}).get('status')!=('passed' if passed else 'failed')
            or any(result.get(k) for k in ('partial','stale','verificationPending'))):return None
    operations=[attempt.get('operationId')]
    if attempt.get('verificationOperationId'):operations.append(attempt['verificationOperationId'])
    expected=[attempt.get('id','')+'/measure',attempt.get('id','')+'/verify']
    if operations not in (expected,expected[:1]) or (passed and operations!=expected):return None
    rows=rt.db.execute('SELECT operation_id,state,request,result FROM platform_effects WHERE workflow_id=? AND attempt_id=?',
        (wf['id'],attempt['id'])).fetchall()
    if {r['operation_id'] for r in rows}!=set(operations):return None
    for row in rows:
        request=json.loads(row['request']) if row['request'] else {}
        raw=json.loads(row['result']) if row['result'] else {}
        if (row['state']!='completed' or request.get('runId')!=wf['id']
                or request.get('nodeId')!=node_id or request.get('attemptId')!=attempt['id']
                or request.get('operationId')!=row['operation_id'] or raw.get('dataKind')!='real'
                or raw.get('partial')):return None
    return {'schema':SCHEMA,'nodeId':node_id,'attemptId':attempt['id'],
        'attemptSha256':digest(attempt),'resultSha256':digest(result),
        'receiptsSha256':digest([{'operationId':r['operation_id'],
            'requestSha256':digest(json.loads(r['request'])),
            'resultSha256':digest(json.loads(r['result']))} for r in sorted(rows,key=lambda r:r['operation_id'])]),
        'operations':operations,'currentNodeId':wf.get('currentNodeId'),
        'nextState':'queued' if passed else 'decision_queued'}


def record(c,rt,wf,node_id):
    """Called inside the existing completion/lease-release transaction."""
    if not is_smart_main(wf):return False
    proof=_proof(c,rt,wf,node_id)
    if proof is None:return False
    wf['smartRun']['mainCheckpoint']={**proof,'completedAt':now()}
    wf.update(status='queued',engineState=proof['nextState'],queueEnteredAt=now())
    return True


def recover(c,rt,wf):
    """Return None for unrelated states; invalid checkpoints remain isolated."""
    if not is_smart_main(wf):return None
    marker=wf.get('smartRun',{}).get('mainCheckpoint') or {}
    queued=(wf['status']=='queued' and marker.get('schema')==SCHEMA
            and wf.get('engineState')==marker.get('nextState')
            and any(n.get('latestAttemptId')==marker.get('attemptId')
                    and n.get('engineState') in ('verified','quality_attention') for n in wf['nodes']))
    running=wf['status']=='running' and wf.get('engineState') in ('acquiring','verifying')
    if not queued and not running:return None
    try:
        proof=_proof(c,rt,wf,marker.get('nodeId') if queued else wf.get('currentNodeId'))
        if proof is None:return 'unsafe'
        if queued:
            return 'valid' if {k:marker.get(k) for k in proof}==proof else 'unsafe'
        wf['smartRun']['mainCheckpoint']={**proof,'completedAt':now()}
        wf.update(status='queued',engineState=proof['nextState'],queueEnteredAt=now())
        return 'restored'
    except (APIError,ValueError,TypeError,KeyError):return 'unsafe'


DECISION_STATES = ('decision_queued', 'deciding')
NODE_DECISION_STATES = (*DECISION_STATES, 'quality_attention')


def _pause_snapshot(c, rt, wf, node):
    parameters, versions = c._parameters(rt, wf, node)
    return digest({'parameters': parameters, 'versions': versions, 'overrides': node.get('parameterOverrides') or {},
                   'fixedReadout': wf.get('readoutRevalidations', {}).get(node.get('resourceId')),
                   'gates': wf.get('gates'), 'gatesVersion': wf.get('gatesVersion')})


def remember_planning(c, rt, wf):
    """Record the intent of a public pause before it replaces the workflow phase."""
    if not is_smart_main(wf) or wf.get('engineState') not in DECISION_STATES:
        return
    node = next((n for n in wf['nodes'] if n['id'] == wf.get('currentNodeId')), {})
    checkpoint = wf['smartRun'].get('mainCheckpoint') or {}
    try:
        settings = _pause_snapshot(c, rt, wf, node) if node else None
    except (APIError, ValueError, KeyError, TypeError):
        settings = None
    wf['smartRun']['pausedPlanning'] = {'schema': 'qcal-paused-planning/1', 'nodeId': node.get('id'),
        'attemptId': node.get('latestAttemptId'), 'checkpointSha256': digest(checkpoint),
        'settingsSha256': settings}


def resume_planning(c, rt, wf):
    """Restore only the right to ask the model about the same closed acquisition."""
    if not is_smart_main(wf):
        return False
    run = wf['smartRun']
    marker = run.get('pausedPlanning')
    node = next((n for n in wf['nodes'] if n['id'] == wf.get('currentNodeId')), None)
    legacy = (marker is None and node and wf['status'] == 'paused' and wf['engineState'] == 'paused'
              and node.get('engineState') in NODE_DECISION_STATES)
    if (c._has_lease(rt, wf['id']) or wf.get('engineState') == 'needs_reconciliation'
            or rt.db.execute("SELECT 1 FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')",
                             (wf['id'],)).fetchone()):
        raise APIError('RECONCILIATION_REQUIRED', '上次设备操作状态未知，请先核查采集回执', 409)
    if not marker and not legacy:
        return False
    def verify(value):
        if not value:
            raise APIError('SMART_PLANNING_CHECKPOINT_CHANGED', '采集回执、参数或工作流已变化，请核对当前节点后重新决策', 409)
    verify(node and wf['status'] == 'paused' and wf['engineState'] == 'paused'
           and node.get('engineState') in NODE_DECISION_STATES and not wf.get('cancelRequested'))
    verify(rt.db.execute('SELECT 1 FROM platform_accounting_workflows WHERE workflow_id=? AND parent_id IS NULL',
                         (wf['id'],)).fetchone())
    checkpoint = run.get('mainCheckpoint') or {}
    if legacy:
        # Old versions have no pause marker. Only a real operator pause and its unchanged failed
        # acquisition can explain an interrupted decision. A generic pause alone cannot authorize it.
        event = rt.db.execute("SELECT event FROM events WHERE run_id=? AND event IN "
                              "('pause','resume','abort','retry','backtrack','parameters_updated') ORDER BY seq DESC LIMIT 1",
                              (wf['id'],)).fetchone()
        verify(event and event[0] == 'pause')
    else:
        verify(marker == {'schema': 'qcal-paused-planning/1', 'nodeId': node['id'],
            'attemptId': node.get('latestAttemptId'), 'checkpointSha256': digest(checkpoint),
            'settingsSha256': _pause_snapshot(c, rt, wf, node)})
    verify(checkpoint.get('schema') == SCHEMA and checkpoint.get('nextState') == 'decision_queued'
           and checkpoint.get('nodeId') == node['id'] and checkpoint.get('attemptId') == node.get('latestAttemptId'))
    # _proof checks original result/receipts, exclusivity, binding and attempt identity. Present its
    # post-acquisition phase locally; the actual workflow stays paused until the full check succeeds.
    shadow_node = {**node, 'engineState': 'quality_attention'}
    shadow = {**wf, 'status': 'queued', 'nodes': [shadow_node if n['id'] == node['id'] else n for n in wf['nodes']]}
    proof = _proof(c, rt, shadow, node['id'])
    verify(proof and all(checkpoint.get(k) == v for k, v in proof.items()))
    attempt = json.loads(rt.db.execute('SELECT payload FROM platform_attempts WHERE workflow_id=? AND id=?',
                                        (wf['id'], node['latestAttemptId'])).fetchone()[0])
    parameters, versions = c._parameters(rt, wf, node)
    fixed = wf.get('readoutRevalidations', {}).get(node.get('resourceId'))
    if fixed and fixed.get('state') in ('active', 'fixed_failed'):
        parameters = c._readout_revalidation_parameters(node, parameters, fixed)
    verify(parameters == attempt.get('parameters') and versions == attempt.get('parameterVersions'))
    node.update(status='attention', engineState='quality_attention', detail='已保留本轮采集，等待智能体根据此次结果决策')
    run.pop('pausedPlanning', None)
    return True

