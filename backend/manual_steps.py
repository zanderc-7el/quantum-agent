"""Human single-step admission and acceptance, persisted in the workflow ledger.

The permit authorizes one target's selection plus independent verification. It
does not authorize accepting results, retries, backtracks or another target.
"""
from copy import deepcopy
import json

from .common import APIError, now
from .backtrack_admission import admit_backtrack


def is_manual(workflow):
    return workflow['mode'] == 'real' and workflow['policy'] == 'strict'


def awaiting_acceptance(workflow):
    return (workflow.get('engineState') == 'awaiting_acceptance'
            or any(n.get('engineState') == 'awaiting_acceptance' for n in workflow['nodes']))


class ManualSteps:
    def _manual_idle(self, rt, wf):
        if (self._has_lease(rt, wf['id']) or wf.get('engineState') == 'needs_reconciliation'
                or rt.db.execute("SELECT 1 FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')", (wf['id'],)).fetchone()):
            raise APIError('RECONCILIATION_REQUIRED', '存在活动或未知采集，先核查设备回执', 409)
        if wf['status'] not in ('draft', 'paused', 'attention'):
            raise APIError('INVALID_STATE', '请等待当前步骤完成或暂停后操作', 409)

    def _manual_dependencies(self, wf, node):
        for dependency in node['dependsOn']:
            prior = self._node(wf, dependency)
            result = prior.get('result') or {}
            if (prior['engineState'] != 'verified' or result.get('stale')
                    or result.get('quality', {}).get('status') != 'passed'
                    or result.get('dataKind') != 'real' or result.get('partial')):
                raise APIError('PREREQUISITES_MISSING', '前置步骤必须是当前工作点已验收的真机结果', 409)

    def _queue_manual_step(self, rt, wf, payload):
        if not is_manual(wf):
            raise APIError('MANUAL_POLICY_REQUIRED', '逐步测量仅适用于人工逐步真机任务', 409)
        self._manual_idle(rt, wf)
        if awaiting_acceptance(wf):
            raise APIError('ACCEPTANCE_REQUIRED', '当前结果待人工确认；确认或明确回退后才能测量', 409)
        if not payload.get('nodeId'):
            raise APIError('NODE_ID_REQUIRED', '逐步测量必须明确选择步骤', 409)
        node = self._node(wf, payload['nodeId'])
        if node['engineState'] == 'verified':
            raise APIError('BACKTRACK_REQUIRED', '已验收步骤须明确回退后才能重测', 409)
        self._manual_dependencies(wf, node)
        if node['capability'] != 'available':
            raise APIError('UNSUPPORTED_METHOD', '当前步骤尚未接入执行器', 409)
        admit_backtrack(wf, node)
        self._assert_adapter_binding(wf)
        parameters, versions = self._parameters(rt, wf, node)
        marker = self._readout_revalidation(rt, wf, node, parameters, versions)
        parameters = self._readout_revalidation_parameters(node, parameters, marker)
        if self._physical_adapter:
            self._validate_plan(wf, node, parameters, versions, payload['operationId'], joint_budget=True, rt=rt)
        previous = wf.get('manualStep')
        if previous:
            wf.setdefault('manualStepHistory', []).append(deepcopy(previous))
        wf['manualStep'] = {'operationId': payload['operationId'], 'targetNodeId': node['id'],
            'state': 'queued', 'requestedRevision': wf['revision'], 'createdAt': now()}
        wf.update(status='queued', engineState='manual_step_queued', currentNodeId=node['id'],
                  cancelRequested=None, approved=False, workflowComplete=False)
        if wf.get('configurationBinding'):
            wf.update(hardwareConfirmed=True, hardwareConfirmedAt=now())

    def _assert_manual_dispatch(self, wf, node_id, attempt_id=None):
        permit = wf.get('manualStep') or {}
        if not permit or permit.get('state') not in ('queued', 'running'):
            raise APIError('MANUAL_STEP_REQUIRED', '请明确选择步骤并发出一次逐步测量许可', 409)
        if permit.get('targetNodeId') != node_id:
            raise APIError('MANUAL_STEP_SCOPE', '本次许可只允许选定的单个步骤', 409)
        if attempt_id is None:
            if permit['state'] != 'queued' or wf['status'] != 'queued' or wf.get('cancelRequested'):
                raise APIError('MANUAL_STEP_REQUIRED', '逐步许可已使用或已停止', 409)
            self._manual_dependencies(wf, self._node(wf, node_id))
            admit_backtrack(wf, self._node(wf, node_id))
        elif permit.get('attemptId') != attempt_id or permit['state'] != 'running':
            raise APIError('MANUAL_STEP_SCOPE', '本次尝试与逐步许可不一致', 409)
        return permit

    def _run_manual_step(self, workflow_id):
        wf = self.get(workflow_id)
        permit = wf.get('manualStep') or {}
        if permit.get('state') != 'queued':
            return
        try:
            self._execute_node(workflow_id, permit['targetNodeId'])
        except APIError as error:
            if error.code not in ('MANUAL_STEP_REQUIRED', 'MANUAL_STEP_SCOPE'):
                raise
        finally:
            with self.store.transaction() as rt:
                current = self.get(workflow_id, rt)
                marker = current.get('manualStep') or {}
                # Only the claimant may close a running permit. A second worker
                # losing the atomic claim must not invalidate the first worker.
                if marker.get('operationId') == permit['operationId'] and marker.get('state') == 'queued':
                    marker.update(state='finished', finishedAt=now())
                    self.store.save(rt, current, 'manual_step_closed', {'message': '本次逐步许可已结束；下一次测量须明确运行本步'})

    def _approve_manual_step(self, rt, wf, payload):
        self._manual_idle(rt, wf)
        node = self._node(wf, payload.get('nodeId'))
        if node['id'] != wf.get('currentNodeId'):
            raise APIError('CURRENT_ACCEPTANCE_REQUIRED', '只能确认当前待验收步骤', 409)
        result = node.get('result') or {}
        if (result.get('quality', {}).get('status') != 'passed' or result.get('stale')
                or result.get('dataKind') != 'real' or result.get('partial')):
            raise APIError('GATE_NOT_PASSED', '确认不能将未通过或未验证结果变为合格', 409)
        if not awaiting_acceptance(wf) or node['engineState'] not in ('awaiting_acceptance', 'verified'):
            raise APIError('CURRENT_ACCEPTANCE_REQUIRED', '当前没有待人工确认的结果', 409)
        row = rt.db.execute('SELECT payload FROM platform_attempts WHERE id=? AND workflow_id=? AND node_id=?',
                            (node.get('latestAttemptId'), wf['id'], node['id'])).fetchone()
        attempt = json.loads(row[0]) if row else {}
        if (attempt.get('result') != result or attempt.get('state') not in ('verified', 'awaiting_acceptance')
                or not attempt.get('verificationOperationId')):
            raise APIError('CURRENT_EVIDENCE_REQUIRED', '当前结果缺少匹配的独立复测尝试记录', 409)
        if payload.get('attemptId') and payload['attemptId'] != attempt['id']:
            raise APIError('STALE_ATTEMPT', '该操作对应的尝试已不是当前尝试', 409)
        for evidence in (result.get('selectionResult', {}), result):
            for artifact in evidence.get('artifacts', []):
                self._artifact(rt, artifact)
        if self._calibration_context_reasons(rt, wf, node, result):
            raise APIError('CURRENT_EVIDENCE_REQUIRED', '结果的校准上下文已改变，须重新测量', 409)
        # Older strict runs may already have committed before their gate. Their
        # stored attempt state identifies that legacy path; never commit twice.
        deferred = attempt['state'] == 'awaiting_acceptance'
        accepted_result = deepcopy(result)
        accepted_at = now()
        node.update(status='success', engineState='verified', mainProcedureComplete=True,
                    humanAcceptedAttemptId=attempt['id'], humanAcceptedAt=accepted_at)
        rebuild = self._commit_candidate(rt, wf, node, result.get('candidateParameters', {}),
                                         attempt.get('parameterVersions', {})) if deferred else None
        if deferred and node['procedureId'] == 'sq_rb' and self._physical_adapter and callable(getattr(self.adapter, 'publish_calibration', None)):
            snapshot = self._validation_snapshot(rt, wf, node, attempt['parameters'], attempt['parameterVersions'])
            snapshot['acceptedEvidence'].append({'procedureId': 'sq_rb', 'status': 'passed',
                'selectionOperationId': attempt['operationId'], 'verificationOperationId': attempt['verificationOperationId'],
                'ruleVersion': wf['gatesVersion']})
            calibration = self.adapter.publish_calibration(snapshot)
            result['artifacts'].append(self._artifact(rt, calibration))
            accepted_result['artifacts'] = deepcopy(result['artifacts'])
            node['calibrationVersion'] = calibration['calibrationVersion']
        attempt.update(state='verified', humanAcceptedAt=accepted_at, result=accepted_result)
        self.store.attempt(rt, wf['id'], attempt)
        if deferred:
            previous = [json.loads(row[0]) for row in rt.db.execute(
                'SELECT payload FROM platform_decisions WHERE workflow_id=? AND node_id=? ORDER BY rowid DESC',
                (wf['id'], node['id']))]
            prior = next((decision for decision in previous
                          if decision.get('source') == 'model' and decision.get('action') == 'retry'), None)
            if prior:
                candidate = self.experience.observe_verified(rt, wf, node, attempt, prior)
                if candidate:
                    node['experienceCandidateId'] = candidate['id']
        marker = self._readout_revalidation(rt, wf, node)
        if (deferred and marker and node['id'] == marker['optimizerNodeId']
                and attempt.get('readoutRevalidation', {}).get('id') == marker['id'] and not rebuild):
            marker.update(state='completed', validationStatus='verified', endedAt=now())
        wf.update(status='paused', engineState='manual_ready', approved=True)
        passed = sum(n['engineState'] == 'verified' for n in wf['nodes'])
        wf['progress'] = round(100 * passed / len(wf['nodes']))
        wf['workflowComplete'] = passed == len(wf['nodes'])
        if wf['workflowComplete']:
            wf.update(status='completed', engineState='verified', progress=100)
            wf['resultSummary'] = {'verifiedNodes': passed, 'totalNodes': len(wf['nodes']),
                'coverage': wf.get('coverage', 'main'), 'recordCount': len(wf['processedRecordIds']),
                'workflowComplete': True, 'meaning': '本轮所选测项全部独立验证并经人工确认'}
        self._record_decision(rt, wf, node['id'], {'action': 'approve', 'reason': '操作员确认当前独立复测结果',
            'evidenceIds': [a['id'] for a in result.get('artifacts', [])],
            'attemptId': attempt['id'], 'rebuildTargetNodeId': rebuild}, 'operator')
