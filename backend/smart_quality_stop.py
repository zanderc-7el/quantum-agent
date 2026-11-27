"""Find a concrete, plan-only alternative for exactly one quality-stop review."""
from copy import deepcopy
from collections import deque

from .backtrack_admission import admit_backtrack
from .common import APIError, digest
from .workflow_budget import only_accounting
from . import smart_sampling

COUNT_KEYS = ('acquisitions_per_point', 'capture_repeats', 'shots', 'rb_captures', 'rb_sequences')
REVIEW_SCHEMA = 'qcal-smart-quality-stop-review/1'


def sampled_counts(rt, workflow, node, key):
    # Only a registered, completed acquisition can establish an observed setting.
    # The left join also exposes attempts lacking an interpretable receipt.
    path = '$.parameters.' + key
    rows = rt.db.execute("""
        SELECT a.id, e.operation_id, e.state, json_extract(e.result,'$.dataKind') AS kind,
               json_extract(e.result,?) AS value, json_extract(e.result,'$.partial') AS partial,
               (json_extract(e.request,'$.runId')=a.workflow_id AND json_extract(e.request,'$.nodeId')=a.node_id
                AND json_extract(e.request,'$.attemptId')=a.id AND json_extract(e.request,'$.operationId')=e.operation_id) AS matched
        FROM platform_attempts a LEFT JOIN platform_effects e ON e.workflow_id=a.workflow_id
             AND e.attempt_id=a.id
        WHERE a.workflow_id=? AND a.node_id=? ORDER BY a.number,e.operation_id
    """, (path, workflow['id'], node['id'])).fetchall()
    if (not rows or len({r['id'] for r in rows}) != node.get('attempt')
            or node.get('latestAttemptId') not in {r['id'] for r in rows}
            or any(r['state'] != 'completed' or r['kind'] != 'real' or r['partial'] or not r['matched']
                   or type(r['value']) is not int or r['value'] < 1 for r in rows)):
        return None
    return {r['value'] for r in rows}, len(rows), digest([(r['operation_id'],r['value']) for r in rows])


def _budget_proof(controller, rt, wf, node, trial, versions):
    plan = controller._validate_plan(wf, node, trial, versions, 'quality-review',
                                     joint_budget=True, rt=rt)
    if not isinstance(plan, dict):
        return None
    return {'planSha256':digest(plan),'reservation':False}


def _nearest_predecessors(wf, node, eligible):
    allowed = set(eligible)
    nodes = {n['id']:n for n in wf['nodes']}
    queue, seen = deque(node['dependsOn']), set()
    while queue:
        key = queue.popleft()
        if key in seen or key not in nodes:
            continue
        seen.add(key)
        if key in allowed:
            yield nodes[key]
        queue.extend(nodes[key]['dependsOn'])


def alternative(controller, rt, wf, node, bounds, targets):
    parameters, versions = controller._parameters(rt, wf, node)
    actual = (node.get('result') or {}).get('parameters') or {}
    native_bounds = controller._parameter_schema(node['procedureId'])
    for key in COUNT_KEYS:
        current = actual.get(key)
        exposed = bounds.get(key) or {}
        native = native_bounds.get(key) or {}
        if (type(current) is not int or current < 1 or parameters.get(key,current) != current
                or type(exposed.get('max')) not in (int,float) or type(native.get('max')) not in (int,float)):
            continue
        versioned = (key == 'acquisitions_per_point' and current == 8 and exposed['max'] == 16
                     and smart_sampling.eligible(wf,node))
        upper = 16 if versioned else min(exposed['max'],native['max'])
        candidate = min(int(upper),current*2)
        if candidate <= current:
            continue
        seen = sampled_counts(rt,wf,node,key)
        if seen is None or candidate in seen[0]:
            continue
        try:
            change = controller._normalize_changes(node['procedureId'], {key:candidate}, parameters)
            trial,release = controller._readout_retry_parameters(rt,wf,node,parameters,versions,change)
            if release or trial.get(key) != candidate:
                continue
            scoped = controller
            if versioned:
                scoped = smart_sampling.authorize_retry(controller,rt,wf,node,trial,versions,
                    {'action':'retry','reason':'预览新的采集块数','parameterChanges':change})
            scoped._validate_parameter(key, candidate, scoped._parameter_schema(node['procedureId']))
            proof = _budget_proof(scoped,rt,wf,node,trial,versions)
        except APIError:
            continue
        if proof:
            return {'action':'retry','parameterChanges':{key:candidate},'from':current,
                    'sourceOperationCount':seen[1],'sourceSettingsSha256':seen[2],**proof}

    # Walking back to a verified dependency can invalidate downstream work.
    # Offer the concrete, admitted option to the model; never perform it here.
    for target in _nearest_predecessors(wf,node,targets):
        result = target.get('result') or {}
        if (target.get('engineState') != 'verified' or result.get('quality',{}).get('status') != 'passed'
                or result.get('stale') or result.get('partial')):
            continue
        try:
            admit_backtrack(wf,target)
            target_params,target_versions = controller._parameters(rt,wf,target)
            trial,_ = controller._readout_retry_parameters(rt,wf,target,target_params,target_versions,{})
            proof = _budget_proof(controller,rt,wf,target,trial,target_versions)
        except APIError:
            continue
        if proof:
            return {'action':'backtrack','targetNodeId':target['id'],**proof}
    return None


def review(controller, rt, wf, node, proposal, bounds, targets):
    from . import model_recovery as recovery
    # prepare_call froze the checkpoint before the reply. Revalidate it and,
    # for a scientific child, authenticate the live parent in this transaction.
    if (not only_accounting(wf) or not wf.get('hardwareConfirmed') or wf.get('cancelRequested')
            or wf['status'] != 'attention' or wf['engineState'] != 'deciding'
            or recovery.device_fault_blocks(wf) or recovery._pending(controller,rt,wf)
            or not recovery._eligible_result(node)):
        return
    recovery._authorized_parent(controller,rt,wf)
    record = wf.get('modelRecovery') or {}
    if record.get('checkpointSha256') != recovery.checkpoint(controller,rt,wf,node)[1]:
        return
    completed = rt.db.execute("""
        SELECT 1 FROM platform_effects WHERE workflow_id=? AND attempt_id=? AND state='completed'
        AND json_extract(request,'$.runId')=workflow_id AND json_extract(request,'$.attemptId')=attempt_id
        AND json_extract(request,'$.operationId')=operation_id AND json_extract(request,'$.nodeId')=?
        AND json_extract(result,'$.dataKind')='real' AND COALESCE(json_extract(result,'$.partial'),0)=0
    """, (wf['id'],node['latestAttemptId'],node['id'])).fetchone()
    if completed is None:
        return
    previous = record.get('qualityStopReplan')
    if previous:
        # The second decision remains the model's. Persist why this review is
        # finished, including which alternative the model actually saw.
        proposal['qualityStopReview'] = {'schema':REVIEW_SCHEMA,'firstAction':previous['firstAction'],
            'alternative':deepcopy(previous['alternative']), 'checkpointSha256':record['checkpointSha256'],
            'outcome':'model_maintained_wait_or_stop_after_review'}
        return
    candidate = alternative(controller,rt,wf,node,bounds,targets)
    if candidate is None:
        return
    record['qualityStopReplan'] = {'schema':REVIEW_SCHEMA,'firstAction':proposal['action'],
        'alternative':candidate, 'instruction':'该方案已通过首测及独立复测计划预检。结合实测证据决定是否采用；如仍应停止或等待，请说明具体实验依据。'}
    description = str(candidate.get('parameterChanges') or candidate.get('targetNodeId'))
    raise APIError('MODEL_REPLAN_REQUIRED', '当前质量未通过；请结合已预检的选项 '+description+' 重新判断一次。原动作未执行。')

