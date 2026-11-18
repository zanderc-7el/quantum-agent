"""Evidence-bound experience suggestions on the controller's existing SQLite.

This module never opens a DB connection, changes a workflow/gate, executes an
instrument, or calls a model. The caller owns transactions and publish authority.
"""
from copy import deepcopy
import json

from .common import APIError, digest, dumps, file_hash, now, number
from .quality import evaluate


class ExperienceLedger:
    def __init__(self, parameter_bounds=None):
        self.parameter_bounds = deepcopy(parameter_bounds or {})

    @staticmethod
    def ensure_schema(rt):
        # execute(), not executescript(): do not commit the caller's transaction.
        for statement in (
            'CREATE TABLE IF NOT EXISTS platform_experience_candidates(id TEXT PRIMARY KEY, attempt_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, payload_hash TEXT NOT NULL, status TEXT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS platform_experience_rules(id TEXT PRIMARY KEY, candidate_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, payload_hash TEXT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS platform_experience_operations(id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, response TEXT NOT NULL)',
        ):
            rt.db.execute(statement)

    def _changes(self, decision):
        changes = decision.get('parameterChanges')
        if not isinstance(changes, dict) or not changes:
            raise APIError('EXPERIENCE_NO_PARAMETER_CHANGE', '经验必须对应明确参数调整')
        clean = {}
        for name, value in changes.items():
            value = value.get('to') if isinstance(value, dict) else value
            bound = self.parameter_bounds.get(name)
            if isinstance(bound, (list, tuple)) and len(bound) == 2:
                bound = {'min': bound[0], 'max': bound[1]}
            if (not isinstance(bound, dict) or not number(value)
                    or not number(bound.get('min')) or not number(bound.get('max'))
                    or not bound['min'] <= value <= bound['max']
                    or (bound.get('integer') and int(value) != value)):
                raise APIError('EXPERIENCE_PARAMETER_OUT_OF_RANGE', '经验参数不在当前冻结边界内：' + str(name))
            clean[name] = value
        return clean

    @staticmethod
    def _raw(rt, result):
        if result.get('dataKind') != 'real' or not result.get('acquiredAt'):
            raise APIError('EXPERIENCE_NOT_REAL', '经验必须有本轮真实采集时间与来源')
        evidence = []
        for artifact in result.get('artifacts', []):
            if artifact.get('kind') != 'raw':
                continue
            row = rt.db.execute('SELECT * FROM platform_artifacts WHERE id=?', (artifact.get('id'),)).fetchone()
            if not row or row['sha256'] != artifact.get('sha256') or file_hash(row['path']) != row['sha256']:
                raise APIError('EXPERIENCE_EVIDENCE_CHANGED', '经验原始证据缺失或哈希不符')
            evidence.append({'id': artifact['id'], 'sha256': row['sha256'], 'kind': 'raw'})
        if not evidence:
            raise APIError('EXPERIENCE_MISSING_RAW', '经验缺少登记的原始采集数据')
        return sorted(evidence, key=lambda a: a['id'])

    def _validate(self, rt, workflow, node, attempt, prior):
        if (workflow.get('mode') != 'real' or attempt.get('mode') != 'real'
                or attempt.get('state') != 'verified' or attempt.get('nodeId') != node.get('id')
                or attempt.get('procedureId') != node.get('procedureId')):
            raise APIError('EXPERIENCE_NOT_VERIFIED', '仅本轮真实且已验证的尝试可形成经验')
        result = attempt.get('result') or {}
        if result.get('quality', {}).get('status') != 'passed':
            raise APIError('EXPERIENCE_NOT_VERIFIED', '经验来源尚未通过独立验证')
        if not isinstance(prior, dict) or prior.get('source') != 'model' or prior.get('action') != 'retry' or prior.get('nodeId') != node['id']:
            raise APIError('EXPERIENCE_NO_MODEL_RETRY', '未找到同一节点的先前模型调整')
        stored_decision = rt.db.execute('SELECT * FROM platform_decisions WHERE id=?', (prior.get('id'),)).fetchone()
        if (not stored_decision or stored_decision['workflow_id'] != workflow['id']
                or digest(json.loads(stored_decision['payload'])) != digest(prior)):
            raise APIError('EXPERIENCE_UNBOUND_DECISION', '模型调整记录未持久绑定')
        stored_attempt = rt.db.execute('SELECT * FROM platform_attempts WHERE id=?', (attempt.get('id'),)).fetchone()
        if (not stored_attempt or stored_attempt['workflow_id'] != workflow['id']
                or digest(json.loads(stored_attempt['payload'])) != digest(attempt)):
            raise APIError('EXPERIENCE_UNBOUND_ATTEMPT', '验证尝试未持久绑定')
        previous_row = rt.db.execute('SELECT * FROM platform_attempts WHERE id=?', (prior.get('attemptId'),)).fetchone()
        if not previous_row or previous_row['workflow_id'] != workflow['id'] or previous_row['node_id'] != node['id']:
            raise APIError('EXPERIENCE_NO_PRIOR_ATTEMPT', '经验缺少调整前的同节点采集')
        previous = json.loads(previous_row['payload'])
        if previous.get('mode') != 'real' or previous['number'] >= attempt['number']:
            raise APIError('EXPERIENCE_NO_PRIOR_ATTEMPT', '调整前后尝试顺序或来源无效')
        prior_ids = {a.get('id') for a in previous.get('result', {}).get('artifacts', [])}
        if not prior.get('evidenceIds') or any(e not in prior_ids for e in prior['evidenceIds']):
            raise APIError('EXPERIENCE_NO_PRIOR_EVIDENCE', '模型调整必须引用前次采集证据')
        changes = self._changes(prior)
        if any(attempt.get('parameters', {}).get(k) != v for k, v in changes.items()):
            raise APIError('EXPERIENCE_CHANGE_NOT_APPLIED', '通过尝试未采用该模型建议，不能归因')
        selection = result.get('selectionResult') or {}
        selected_raw, verified_raw = self._raw(rt, selection), self._raw(rt, result)
        if {a['sha256'] for a in selected_raw} & {a['sha256'] for a in verified_raw}:
            raise APIError('EXPERIENCE_NOT_INDEPENDENT', '选择与验收数据不是两次独立采集')
        operations = [attempt.get('operationId'), attempt.get('verificationOperationId')]
        if not all(operations) or operations[0] == operations[1]:
            raise APIError('EXPERIENCE_MISSING_RECEIPTS', '缺少独立选择与复测操作编号')
        receipts = []
        for operation, expected in zip(operations, (selected_raw, verified_raw)):
            row = rt.db.execute('SELECT * FROM platform_effects WHERE operation_id=?', (operation,)).fetchone()
            if not row or row['workflow_id'] != workflow['id'] or row['attempt_id'] != attempt['id'] or row['state'] != 'completed':
                raise APIError('EXPERIENCE_MISSING_RECEIPTS', '采集回执未完成或绑定错误')
            receipt = json.loads(row['result'])
            if self._raw(rt, receipt) != expected:
                raise APIError('EXPERIENCE_RECEIPT_MISMATCH', '采集回执与验收证据不一致')
            receipts.append({'operationId': operation, 'resultSha256': digest(receipt)})
        gates = workflow.get('gates') or {}
        assessed = evaluate(result, node['procedureId'], 'real', gates, independent=True)
        if assessed['quality']['status'] != 'passed':
            raise APIError('EXPERIENCE_GATE_FAILED', '原始指标未满足该任务冻结门槛')
        return {'parameterChanges': changes,
                'scope': {'resourceId': node['resourceId'], 'ownerId': node['ownerId'],
                          'procedureId': node['procedureId'], 'blueprintVersion': workflow['blueprintVersion']},
                'provenance': {'workflowId': workflow['id'], 'nodeId': node['id'], 'attemptId': attempt['id'],
                    'priorAttemptId': previous['id'], 'decisionId': prior['id'], 'source': 'model_retry_then_independent_real_verification',
                    'attemptSha256': digest(attempt), 'decisionSha256': digest(prior),
                    'parameterVersions': deepcopy(attempt.get('parameterVersions', {})),
                    'parameters': deepcopy(attempt.get('parameters', {})),
                    'gatesVersion': workflow.get('gatesVersion'), 'gatesSha256': digest(gates), 'receipts': receipts},
                'verification': {'status': 'passed', 'selection': selected_raw, 'independent': verified_raw,
                    'metrics': deepcopy(result.get('metrics', {})), 'criteria': deepcopy(assessed['quality']['criteria']),
                    'acquiredAt': result['acquiredAt']}}

    def observe_verified(self, rt, workflow, node, attempt, priorDecision):
        self.ensure_schema(rt)
        if node.get('engineState') != 'verified' or node.get('latestAttemptId') != attempt.get('id'):
            return None
        try:
            evidence = self._validate(rt, workflow, node, attempt, priorDecision)
        except (APIError, OSError, ValueError, KeyError, TypeError):
            return None
        existing = rt.db.execute('SELECT * FROM platform_experience_candidates WHERE attempt_id=?', (attempt['id'],)).fetchone()
        if existing:
            return {**json.loads(existing['payload']), 'status': existing['status']}
        candidate = {'id': 'experience-' + digest([workflow['id'], attempt['id'], priorDecision['id']])[:32],
                     'schema': 'qcal-experience/1', 'status': 'candidate', 'createdAt': now(),
                     'title': node.get('label', node['procedureId']) + '：参数调整后独立复测通过',
                     'reason': priorDecision['reason'], 'boundsSha256': digest(self.parameter_bounds), **evidence}
        rt.db.execute('INSERT INTO platform_experience_candidates VALUES(?,?,?,?,?)',
                      (candidate['id'], attempt['id'], dumps(candidate), digest(candidate), 'candidate'))
        return candidate

    def list(self, rt):
        self.ensure_schema(rt)
        return [{**json.loads(row['payload']), 'status': row['status']} for row in
                rt.db.execute('SELECT * FROM platform_experience_candidates ORDER BY rowid DESC')]

    def publish(self, rt, candidateId, operationId):
        self.ensure_schema(rt)
        if not isinstance(operationId, str) or not 1 <= len(operationId) <= 256:
            raise APIError('OPERATION_ID_REQUIRED', '经验发布需要唯一 operationId')
        prior = rt.db.execute('SELECT * FROM platform_experience_operations WHERE id=?', (operationId,)).fetchone()
        if prior:
            if prior['candidate_id'] != candidateId:
                raise APIError('IDEMPOTENCY_CONFLICT', '同一操作编号不能发布不同经验', 409)
            return json.loads(prior['response'])
        row = rt.db.execute('SELECT * FROM platform_experience_candidates WHERE id=?', (candidateId,)).fetchone()
        if not row:
            raise APIError('EXPERIENCE_NOT_FOUND', '经验候选不存在', 404)
        candidate = json.loads(row['payload'])
        if digest(candidate) != row['payload_hash']:
            raise APIError('EXPERIENCE_RECORD_CHANGED', '经验候选内容哈希改变', 409)
        source = candidate['provenance']
        wf_row = rt.db.execute('SELECT payload FROM platform_workflows WHERE id=?', (source['workflowId'],)).fetchone()
        attempt_row = rt.db.execute('SELECT payload FROM platform_attempts WHERE id=?', (source['attemptId'],)).fetchone()
        decision_row = rt.db.execute('SELECT payload FROM platform_decisions WHERE id=?', (source['decisionId'],)).fetchone()
        if not all((wf_row, attempt_row, decision_row)):
            raise APIError('EXPERIENCE_SOURCE_MISSING', '经验来源记录已丢失', 409)
        workflow, attempt, decision = (json.loads(r['payload']) for r in (wf_row, attempt_row, decision_row))
        node = next((n for n in workflow['nodes'] if n['id'] == source['nodeId']), None)
        if not node:
            raise APIError('EXPERIENCE_SOURCE_MISSING', '来源节点已丢失', 409)
        refreshed = self._validate(rt, workflow, node, attempt, decision)
        if any(digest(refreshed[key]) != digest(candidate[key]) for key in refreshed):
            raise APIError('EXPERIENCE_SOURCE_CHANGED', '经验验证来源或冻结门槛已变化', 409)
        existing = rt.db.execute('SELECT * FROM platform_experience_rules WHERE candidate_id=?', (candidateId,)).fetchone()
        if existing:
            rule = json.loads(existing['payload'])
        else:
            rule = {'id': 'rule-' + candidateId, 'version': 1, 'title': candidate['title'],
                'scope': deepcopy(candidate['scope']),
                'trigger': {'kind': 'similar_quality_failure', 'scopeRequired': True,
                            'baselineParameterVersions': deepcopy(source['parameterVersions'])},
                'action': {'kind': 'suggest_parameters', 'parameterChanges': deepcopy(candidate['parameterChanges']),
                           'requiresFreshVerification': True, 'automaticExecution': False},
                'verification': deepcopy(candidate['verification']),
                'evidence': [a['id'] for a in candidate['verification']['selection'] + candidate['verification']['independent']],
                'provenance': deepcopy(source), 'candidateId': candidateId, 'publishedAt': now(),
                'publicationBoundsSha256': digest(self.parameter_bounds),
                'note': '限已记录资源和方法的建议；新任务仍须遵守当前参数边界及冻结验收门槛'}
            rt.db.execute('INSERT INTO platform_experience_rules VALUES(?,?,?,?)', (rule['id'], candidateId, dumps(rule), digest(rule)))
            rt.db.execute("UPDATE platform_experience_candidates SET status='published' WHERE id=?", (candidateId,))
        rt.db.execute('INSERT INTO platform_experience_operations VALUES(?,?,?)', (operationId, candidateId, dumps(rule)))
        return rule

    def published_rules(self, rt):
        self.ensure_schema(rt)
        rules = []
        for row in rt.db.execute('SELECT * FROM platform_experience_rules ORDER BY rowid'):
            rule = json.loads(row['payload'])
            if digest(rule) != row['payload_hash']:
                raise APIError('EXPERIENCE_RECORD_CHANGED', '发布规则哈希不一致', 409)
            try:
                self._changes({'parameterChanges': rule['action']['parameterChanges']})
            except APIError:
                continue
            rules.append(rule)
        return rules
