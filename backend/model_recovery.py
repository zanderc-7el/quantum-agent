"""Persistent model-only replanning for explicitly authorized smart Q11 runs.

This module never calls an adapter, changes physical gates, creates an effect or
acquires a lease. It can only requeue the existing validated decision path.
"""
from copy import deepcopy
from contextlib import closing
import json
import math
import sqlite3
import threading
import time

from .common import APIError, digest, now
from .workspace import device_fault_blocks

SCHEMA = 'qcal-smart-model-recovery/1'
SMART_SCOPE = 'q11-full-same-gate/1'
MAX_BACKOFF_SECONDS = 300
MAX_INFLIGHT = 4
MODEL_WAIT_SECONDS = 185
REPLAN_ERRORS = frozenset({
    'BAD_DECISION', 'BAD_EVIDENCE', 'BAD_PARAMETERS', 'BAD_BACKTRACK', 'STALE_MODEL_DECISION',
    'PARAMETER_OUT_OF_RANGE', 'UNKNOWN_PARAMETER', 'INVALID_PARAMETER', 'INVALID_SCAN',
    'INVALID_PARAMETER_NORMALIZATION', 'INEFFECTIVE_PARAMETER_CHANGE', 'GATE_NOT_PASSED',
    'PULSE_GRID', 'PULSE_POLICY_MISMATCH', 'SEQUENCE_WINDOW', 'RB_SEQUENCE_WINDOW',
    'BUDGET_EXCEEDED', 'SAMPLING_ARTIFACT_BUDGET', 'MODEL_REPLAN_REQUIRED',
})
UNRECOVERABLE_MODEL_ERRORS = frozenset({'context_budget_exceeded', 'input_too_large', 'input_too_deep'})
_LOCK = threading.Lock()
_INFLIGHT = {}


def enabled(workflow):
    main = (workflow.get('mode') == 'real' and workflow.get('policy') == 'autopilot'
            and workflow.get('resourceIds') == ['Q11']
            and isinstance(workflow.get('adapterProfileHash'), str) and bool(workflow['adapterProfileHash'])
            and workflow.get('smartRun', {}).get('schema') == SMART_SCOPE
            and workflow['smartRun'].get('adapterProfileHash') == workflow.get('adapterProfileHash'))
    # This is only a scope filter. Child admission additionally authenticates
    # its live parent in prepare_call/child_decision_ready on every invocation.
    child = (workflow.get('mode') == 'real' and workflow.get('policy') == 'strict'
             and workflow.get('resourceIds') == ['Q11']
             and workflow.get('smartParent', {}).get('schema') == 'qcal-smart-science-child/1')
    return main or child


def _authorized_parent(controller, rt, workflow):
    if workflow.get('smartParent'):
        from .smart_science import assert_authorized
        return assert_authorized(controller, workflow, rt=rt)
    return None


def _eligible_result(node):
    result = node.get('result') or {}
    return (isinstance(node.get('latestAttemptId'), str) and result.get('dataKind') == 'real'
            and not result.get('partial') and not result.get('stale')
            and result.get('quality', {}).get('status') in ('failed', 'unverified'))


def checkpoint(controller, rt, workflow, node):
    parameters, versions = controller._parameters(rt, workflow, node)
    value = {'workflowId': workflow['id'], 'nodeId': node['id'],
             'attemptId': node.get('latestAttemptId'), 'attemptNumber': node.get('attempt'),
             'resultSha256': digest(node.get('result')), 'parametersSha256': digest(parameters),
             'parameterVersionsSha256': digest(versions),
             'adapterId': workflow.get('adapterId'), 'adapterProfileHash': workflow.get('adapterProfileHash'),
             'gatesSha256': digest(workflow.get('gates')), 'gatesVersion': workflow.get('gatesVersion')}
    from .smart_rabi_window import PROPOSAL_POLICY
    from .smart_sampling import eligible
    if eligible(workflow,node):
        value['proposalRules']={'smartRabiWindow':PROPOSAL_POLICY}
    return value, digest(value)


def _pending(controller, rt, workflow):
    return (controller._has_lease(rt, workflow['id'])
            or rt.db.execute("SELECT 1 FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')",
                             (workflow['id'],)).fetchone() is not None)


def proposal_identity(proposal):
    if not isinstance(proposal, dict):
        return {'action': 'invalid_shape'}
    changes = proposal.get('parameterChanges')
    bounded = {}
    if isinstance(changes, dict):
        for key, value in list(changes.items())[:32]:
            if not isinstance(key, str) or len(key) > 96:
                continue
            value = value.get('to') if isinstance(value, dict) else value
            if isinstance(value, list):
                # Array-valued scan axes are real proposals too. Preserve a
                # bounded exact snapshot so shortening RB does not collide
                # with an earlier rejected long sequence.
                def scalar(item):
                    if item is None or type(item) in (bool, int): return True
                    if type(item) is str: return len(item) <= 128
                    return type(item) is float and math.isfinite(item)
                bounded[key] = deepcopy(value) if len(value) <= 256 and all(scalar(v) for v in value) else {'invalid': 'unsupported array'}
                continue
            if value is None or type(value) in (str, int, float, bool):
                if isinstance(value, str):
                    value = value[:128]
                bounded[key] = value
    value = {'action': str(proposal.get('action'))[:32],
             'targetNodeId': str(proposal.get('targetNodeId', ''))[:240], 'parameterChanges': bounded}
    # A corrected citation can make a previously invalid proposal legitimate.
    # Keep a bounded digest, not another large copy of raw artifact metadata.
    evidence = proposal.get('evidenceIds')
    value['evidenceIdsSha256'] = digest(sorted(str(item)[:240] for item in evidence[:100])) if isinstance(evidence, list) else None
    try:
        digest(value)
    except (ValueError, TypeError, OverflowError):
        value['parameterChanges'] = {'invalid': 'non-finite or non-JSON value'}
    return value


def attach_feedback(payload, workflow):
    if not enabled(workflow):
        return payload
    record = workflow.get('modelRecovery') or {}
    feedback = {'schema': SCHEMA, 'policy': 'Replan only; all original physical gates and admission checks remain mandatory.',
                'instruction': '前一方案未执行。请根据错误、当前实测与有效参数重新规划；不能重复同一失败方案，不能绕过物理门槛。'}
    if record.get('schema') == SCHEMA:
        feedback.update({key: deepcopy(record[key]) for key in ('failureCount', 'lastError', 'rejectedProposals') if key in record})
    payload['modelRecovery'] = feedback  # Deliberately outside evidence-ID observations/history.
    return payload


def prepare_call(controller, payload):
    """Persist the model checkpoint before HTTP; a restart can resume planning only."""
    with controller.store.transaction() as rt:
        workflow = controller.get(payload['runId'], rt)
        if not enabled(workflow):
            return payload
        if workflow['revision'] != payload['revision']:
            return None
        parent = _authorized_parent(controller, rt, workflow)
        node = controller._node(workflow, payload['nodeId'])
        if (controller.stop_event.is_set() or workflow.get('cancelRequested')
                or workflow['status'] != 'attention' or workflow['engineState'] != 'deciding'
                or device_fault_blocks(workflow) or _pending(controller, rt, workflow)
                or not _eligible_result(node)):
            return None
        value, key = checkpoint(controller, rt, workflow, node)
        previous = workflow.get('modelRecovery') or {}
        if (previous.get('schema') == SCHEMA and previous.get('checkpointSha256') != key
                and value.get('proposalRules') and previous.get('checkpoint',{}).get('proposalRules') != value['proposalRules']):
            # An explicit new decision uses the current validator. Keep the old
            # rejection as history; never execute or auto-resubmit that proposal.
            workflow['modelRecoveryHistory']=(workflow.get('modelRecoveryHistory',[])+[
                {'reason':'proposal_rules_changed','record':deepcopy(previous),'at':now()}])[-8:]
        record = deepcopy(previous) if previous.get('schema') == SCHEMA and previous.get('checkpointSha256') == key else {
            'schema': SCHEMA, 'checkpoint': value, 'checkpointSha256': key, 'failureCount': 0, 'rejectedProposals': []}
        request_id = digest({'checkpoint': key, 'revision': workflow['revision']})
        record.update(state='calling', requestId=request_id, nextAttemptAt=None, startedAt=now())
        workflow['modelRecovery'] = record
        controller.store.save(rt, workflow, 'model_request_checkpoint',
                              {'nodeId': node['id'], 'attemptId': node.get('latestAttemptId'),
                               'requestId': request_id, 'message': '保存当前证据与模型请求检查点；未创建采集许可'})
        payload['revision'] = workflow['revision']
        # Purge feedback prepared before we could validate the live checkpoint.
        for field in ('failureCount', 'lastError', 'rejectedProposals', 'qualityStopReplan'):
            payload.setdefault('modelRecovery', {}).pop(field, None)
            if field in record:
                payload['modelRecovery'][field] = deepcopy(record[field])
        payload.setdefault('modelRecovery', {}).update(requestId=request_id, checkpointSha256=key)
        if parent is not None:
            payload['modelRecovery']['parentCheckpoint'] = {'workflowId': parent['id'], 'revision': parent['revision']}
    return payload


def call_model(controller, payload):
    """Bounded daemon HTTP wait, cancellable by any superseding workflow revision."""
    if not payload.get('modelRecovery', {}).get('requestId'):
        return controller._brain('/decide', payload)
    workflow_id = payload['runId']
    with _LOCK:
        if workflow_id in _INFLIGHT or len(_INFLIGHT) >= MAX_INFLIGHT:
            return {'status': 'waiting_model', 'error': {'code': 'MODEL_CALL_INFLIGHT'},
                    'message': '已有模型请求正在结束；保持同一检查点，稍后再规划'}
        entry = {'event': threading.Event(), 'response': None, 'requestId': payload['modelRecovery']['requestId']}
        _INFLIGHT[workflow_id] = entry
    def execute():
        try:
            entry['response'] = controller._brain('/decide', payload)
        except Exception as exc:
            entry['response'] = {'status': 'waiting_model', 'error': {'code': 'MODEL_SERVICE_ERROR'},
                                 'message': '模型服务请求未完成：' + str(exc)[:256]}
        finally:
            entry['event'].set()
            with _LOCK:
                if _INFLIGHT.get(workflow_id) is entry:
                    _INFLIGHT.pop(workflow_id, None)
    threading.Thread(target=execute, name='qcal-model-read-' + workflow_id[-12:], daemon=True).start()
    deadline = time.monotonic() + MODEL_WAIT_SECONDS
    while not entry['event'].wait(.1):
        if controller.stop_event.is_set():
            return {'status': 'superseded', 'message': '服务停止，模型回复不再执行'}
        # Every workflow mutation increments revision. Reading that scalar avoids
        # repeatedly rendering the entire acquisition archive while HTTP waits.
        with closing(sqlite3.connect(controller.store.path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            current = db.execute('SELECT revision FROM platform_workflows WHERE id=?', (workflow_id,)).fetchone()
            parent = payload.get('modelRecovery', {}).get('parentCheckpoint')
            parent_row = (db.execute('SELECT revision FROM platform_workflows WHERE id=?', (parent['workflowId'],)).fetchone()
                          if parent else None)
        if (current is None or current[0] != payload['revision']
                or parent and (parent_row is None or parent_row[0] != parent['revision'])):
            return {'status': 'superseded', 'message': '用户操作已取代本次模型请求'}
        if time.monotonic() >= deadline:
            return {'status': 'waiting_model', 'error': {'code': 'MODEL_WAIT_TIMEOUT'},
                    'message': '模型请求超时，保留检查点并退避；未下发采集'}
    return entry['response']


def record_failure(controller, rt, workflow, node, payload, *, code, message, proposal=None, transient=False):
    if not enabled(workflow):
        return
    value, key = checkpoint(controller, rt, workflow, node)
    record = workflow.get('modelRecovery') or {}
    if record.get('schema') != SCHEMA or record.get('checkpointSha256') != key:
        record = {'schema': SCHEMA, 'checkpoint': value, 'checkpointSha256': key,
                  'failureCount': 0, 'rejectedProposals': []}
    request_id = payload.get('modelRecovery', {}).get('requestId') or digest({'revision': payload['revision'], 'checkpoint': key})
    if record.get('lastFailureRequestId') == request_id:
        return
    count = int(record.get('failureCount', 0)) + 1
    delay = min(MAX_BACKOFF_SECONDS, 5 * 2 ** min(count - 1, 6))
    allowed = (transient and code not in UNRECOVERABLE_MODEL_ERRORS) or code in REPLAN_ERRORS
    allowed = allowed and _eligible_result(node) and not workflow.get('cancelRequested') and not device_fault_blocks(workflow) and not _pending(controller, rt, workflow)
    try:
        _authorized_parent(controller, rt, workflow)
    except APIError:
        allowed = False
    record.update(state='backoff' if allowed else 'blocked', failureCount=count,
                  nextAttemptAt=time.time() + delay if allowed else None,
                  lastError={'code': str(code)[:96], 'message': str(message)[:512]},
                  lastFailureRequestId=request_id, failedAt=now())
    if proposal is not None:
        value = proposal_identity(proposal)
        item = {'sha256': digest(value), 'proposal': value, 'errorCode': str(code)[:96]}
        record['rejectedProposals'] = ([r for r in record.get('rejectedProposals', []) if r['sha256'] != item['sha256']] + [item])[-8:]
    workflow['modelRecovery'] = record
    if allowed:
        node['detail'] = str(message)[:512] + f'；将在 {delay} 秒后基于同一证据重新规划，尚未下发采集。'


def guard_proposal(workflow, proposal, *, controller=None, rt=None, node=None, bounds=None, targets=()):
    if not enabled(workflow):
        return
    if controller is not None and proposal.get('action') in ('wait', 'stop'):
        from .smart_quality_stop import review
        review(controller, rt, workflow, node, proposal, bounds or {}, targets)
        return
    signature = digest(proposal_identity(proposal))
    if any(r.get('sha256') == signature for r in workflow.get('modelRecovery', {}).get('rejectedProposals', [])):
        raise APIError('MODEL_REPLAN_REQUIRED', '相同方案已被当前检查点拒绝，请改用有效参数或其他受允许的路径')


def guard_retry(controller, rt, workflow, node, trial):
    if not enabled(workflow):
        return
    parameters, _ = controller._parameters(rt, workflow, node)
    actual = (node.get('result') or {}).get('parameters') or {}
    before, after = {**actual, **parameters}, {**actual, **trial}
    for value in (before, after):
        value.pop('order_seed', None)
    if digest(before) == digest(after):
        raise APIError('MODEL_REPLAN_REQUIRED', '当前失败结果下没有改变实际采集或扫描参数，不能盲目重复同一测量')


def record_resolution(workflow, decision):
    if enabled(workflow) and workflow.get('modelRecovery', {}).get('schema') == SCHEMA:
        workflow['modelRecovery'].update(state='resolved', nextAttemptAt=None,
                                        resolvedAt=now(), decisionAction=decision['action'])


def _recovery_checkpoint(controller, rt, workflow, blockers, record):
    """Read-only proof shared by the scheduler and the human-attention projection."""
    if device_fault_blocks(workflow) or controller._shared_blocker(workflow, blockers) or _pending(controller, rt, workflow):
        return None
    saved=record.get('checkpoint')
    if not isinstance(saved,dict):return None
    node_id=saved.get('nodeId')
    node=next((n for n in workflow['nodes'] if n['id']==node_id),None)
    if node is None or node_id!=workflow.get('currentNodeId') or not _eligible_result(node):
        return None
    try:
        scoped=controller.for_workflow(workflow['id'],rt)
        scoped._assert_adapter_binding(workflow)
        value,key=checkpoint(scoped,rt,workflow,node)
    except APIError:
        return None
    return node,value,key


def has_scheduled_backoff(controller, rt, workflow, workflows):
    """Hide a prompt only while a permitted smart retry has its exact frozen evidence."""
    from .workflow_budget import only_accounting, remaining, CEILINGS
    if (not enabled(workflow) or workflow.get('status')!='attention' or workflow.get('engineState')!='waiting_model'
            or workflow.get('cancelRequested') or controller.stop_event.is_set() or not only_accounting(workflow)):
        return False
    record=workflow.get('modelRecovery') or {}
    due=record.get('nextAttemptAt')
    if (record.get('schema')!=SCHEMA or record.get('state')!='backoff' or type(due) not in (int,float)
            or not math.isfinite(due) or due<=0 or due>time.time()+MAX_BACKOFF_SECONDS+1):
        return False
    row=rt.db.execute('SELECT parent_id FROM platform_accounting_workflows WHERE workflow_id=?',(workflow['id'],)).fetchone()
    if not row:
        return False
    try:
        # A malformed/changed finite budget must stay visible. No reservation is created.
        for key in CEILINGS:remaining(workflow,key,0)
        if workflow.get('policy')=='strict':
            parent=_authorized_parent(controller,rt,workflow)
            if not parent or parent['id']!=row[0]:return False
            if not rt.db.execute('SELECT 1 FROM platform_accounting_workflows WHERE workflow_id=? AND parent_id IS NULL',(parent['id'],)).fetchone():
                return False
        elif workflow.get('policy')!='autopilot' or row[0] is not None:
            return False
        eligibility=_recovery_checkpoint(controller,rt,workflow,workflows,record)
    except (APIError,KeyError,ValueError,TypeError,OSError):
        return False
    return bool(eligibility and record.get('checkpoint')==eligibility[1] and record.get('checkpointSha256')==eligibility[2])


def recover_due(controller, *, at=None):
    """Queue model decisions only, once per persisted checkpoint. No device calls."""
    if controller.stop_event.is_set():
        return 0
    if at is None:
        monotonic = time.monotonic()
        if monotonic < getattr(controller, '_next_model_recovery_check', 0):
            return 0
        controller._next_model_recovery_check = monotonic + 1
    at = time.time() if at is None else at
    # No Runtime construction, workflow decoding or write transaction for old
    # installations/no opted-in task. The accounting registration is made by
    # the normal smart launch in the same transaction as its authorization.
    with closing(sqlite3.connect(controller.store.path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        ids = [r[0] for r in db.execute('''
            SELECT w.id FROM platform_accounting_workflows a
            JOIN platform_workflows w ON w.id=a.workflow_id
            WHERE a.parent_id IS NULL
              AND json_extract(w.payload,'$.status')='attention'
              AND json_extract(w.payload,'$.modelRecovery.schema')=?
              AND json_extract(w.payload,'$.modelRecovery.state') IN ('backoff','calling')
              AND json_extract(w.payload,'$.engineState') IN ('waiting_model','deciding')
              AND (json_extract(w.payload,'$.modelRecovery.state')='calling'
                   OR (json_type(w.payload,'$.modelRecovery.nextAttemptAt') IN ('integer','real')
                       AND json_extract(w.payload,'$.modelRecovery.nextAttemptAt') <= ?))
        ''', (SCHEMA, at))]
    if not ids:
        return 0
    queued = 0
    with controller.store.transaction() as rt:
        workflows = [controller.get(identifier, rt) for identifier in ids]
        blockers = [json.loads(r[0]) for r in rt.db.execute('''SELECT payload FROM platform_workflows
            WHERE json_extract(payload,'$.mode')='real'
              AND json_extract(payload,'$.status') IN ('attention','paused')''')]
        for workflow in workflows:
            if (not enabled(workflow) or workflow.get('policy') != 'autopilot'
                    or workflow['status'] != 'attention' or workflow.get('cancelRequested')):
                continue
            record = workflow.get('modelRecovery') or {}
            if record.get('schema') != SCHEMA or record.get('state') not in ('backoff', 'calling'):
                continue
            if workflow['engineState'] not in ('waiting_model', 'deciding'):
                continue
            eligibility = _recovery_checkpoint(controller, rt, workflow, blockers, record)
            if eligibility is None:
                continue
            node, value, key = eligibility
            if key != record.get('checkpointSha256') or value != record.get('checkpoint'):
                record.update(state='superseded', nextAttemptAt=None)
                controller.store.save(rt, workflow, 'model_recovery_superseded', {'message': '实测证据或参数已改变，旧模型检查点停止恢复'})
                continue
            with _LOCK:
                active = workflow['id'] in _INFLIGHT
            if active:
                continue
            if record['state'] == 'calling':
                record.update(state='backoff', nextAttemptAt=at + 5,
                              lastError={'code': 'MODEL_REQUEST_INTERRUPTED', 'message': '模型请求被中断；先恢复规划检查点，不重发仪器命令'})
                workflow['engineState'] = node['engineState'] = 'waiting_model'
                controller.store.save(rt, workflow, 'model_checkpoint_recovered', {'message': record['lastError']['message']})
                continue
            if not isinstance(record.get('nextAttemptAt'), (int, float)) or record['nextAttemptAt'] > at:
                continue
            record.update(state='queued', nextAttemptAt=None, queuedAt=now())
            workflow.update(status='queued', engineState='decision_queued', queueEnteredAt=now())
            node.update(status='attention', engineState='decision_queued', detail='退避已结束，重新请求有效模型方案；未下发采集')
            controller.store.save(rt, workflow, 'model_recovery_queued',
                                  {'message': node['detail'], 'nodeId': node['id'], 'checkpointSha256': key})
            queued += 1
    if queued:
        controller.wake.set()
    return queued


def child_decision_ready(controller, child, parent_id, *, at=None):
    """The authenticated science scheduler owns strict-child requests/permits.

    Return eligibility only; never enqueue the generic autopilot dispatcher.
    A valid model wait/stop is a resolved decision, not a transient failure.
    """
    from .smart_science import assert_authorized
    at = time.time() if at is None else at
    with controller.store.transaction() as rt:
        current = controller.get(child['id'], rt)
        assert_authorized(controller, current, parent_id, rt)
        if (controller.stop_event.is_set() or current.get('cancelRequested')
                or current['status'] != 'attention'
                or current['engineState'] not in ('quality_attention', 'waiting_model', 'deciding')
                or device_fault_blocks(current) or _pending(controller, rt, current)):
            return False
        node = current['nodes'][0]
        if not _eligible_result(node):
            return False
        _, key = checkpoint(controller, rt, current, node)
        record = current.get('modelRecovery') or {}
        if record.get('schema') != SCHEMA or record.get('checkpointSha256') != key:
            return current['engineState'] == 'quality_attention'
        with _LOCK:
            if current['id'] in _INFLIGHT:
                return False
        if record.get('state') == 'calling':
            record.update(state='backoff', nextAttemptAt=at + 5,
                          lastError={'code': 'MODEL_REQUEST_INTERRUPTED', 'message': '科学模型请求中断，恢复规划检查点；未重发采集'})
            current['engineState'] = node['engineState'] = 'waiting_model'
            controller.store.save(rt, current, 'model_checkpoint_recovered', {'message': record['lastError']['message']})
            return False
        due = record.get('nextAttemptAt')
        return record.get('state') == 'backoff' and type(due) in (int, float) and due <= at
