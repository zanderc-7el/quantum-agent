from __future__ import annotations

from copy import deepcopy
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
import time
import types
import urllib.error
import urllib.parse
import urllib.request

from .common import APIError, ROOT, checked_path, digest, dumps, file_hash, ident, now, number
from .chat_acquisition_context import read_chat_snapshot, acquisition_context, bound_chat_observations
from .curve_context import attach_curve_summary
from .capture_diagnostics import (PROCEDURES as CAPTURE_DIAGNOSTIC_PROCEDURES,
                                  resolve_sources, calculate_diagnostics, attach_diagnostics)
from .acquisition_budget import verification_probe, selection_lookahead, verify_admission, sampling_context
from .backtrack_admission import admit_backtrack, eligible_backtracks
from .accepted_upstream_context import resolve_accepted_upstream, attach_accepted_upstream
from .rb_diagnostic import build_proof as rb_diagnostic_proof, VERSION as RB_DIAGNOSTIC_VERSION, require as rb_require
from .model_input_projection import project_model_input, MAX_INPUT_UNITS as MODEL_INPUT_MAX_UNITS
from .readout_withdrawal import restoration_source, VERSION as READOUT_WITHDRAWAL_VERSION, READOUT_KEYS
from .catalog import Catalog, VERSION, PARAMETERS, WORKING_POINT_VERSION, WORKING_POINT_OMITTED
from .working_point import build_working_point_seed
from .quality import GATES, evaluate, validate_proposal
from .decision_context import build_decision_context
from .model_parameter_semantics import build_model_parameter_contract
from .prior_failure_context import (PROFILE_FILES, CHECKED_JSON_NAMES, MAX_CASES, MAX_CANDIDATES,
                                    audited_bindings, eligible_header, project_failure_case, bounded_context, attach_prior_context,
                                    javascript_json_units_upper_bound)
from .store import Store, StepSpec, Budget
from .result_archive import ResultArchive, record_effect
from .experience import ExperienceLedger

TERMINAL = {'completed', 'failed', 'aborted'}
PARAMETER_ALIASES = {'readout_frequency_hz': 'readout_hz', 'drive_frequency_hz': 'drive_hz',
                     'drive_amplitude': 'drive_amp', 'pi_duration_s': 'drive_length_s',
                     'readout_amplitude': 'readout_amp'}
DEFAULT_ADAPTER = 'arclight-native-cy8'
CALIBRATION_KEYS = ('readout_hz', 'readout_amp', 'drive_hz', 'drive_amp', 'drive_length_s', 'half_pi_amp', 'xy_delay_s')


def configured_adapter(root):
    """Load one explicitly allowlisted adapter without importing instrument code."""
    root = checked_path(root)
    local = root / 'config' / 'runtime.local.json'
    settings = json.loads(local.read_text(encoding='utf-8-sig')) if local.is_file() else {}
    selected = settings.get('adapter', DEFAULT_ADAPTER)
    profiles = {'arclight-native-cy8': 'adapters/arclight_profile.json', 'f8-native-cy8': 'adapters/f8_profile.json'}
    if not isinstance(selected, str) or selected not in profiles:
        raise APIError('UNKNOWN_ADAPTER', '运行配置必须选择 arclight-native-cy8 或 f8-native-cy8')
    profile_value = settings.get('profile', profiles[selected])
    if selected == 'f8-native-cy8' and 'profile' not in settings and (root / 'config' / 'f8.local.json').is_file():
        profile_value = 'config/f8.local.json'
    if not isinstance(profile_value, str):
        raise APIError('INVALID_ADAPTER_PROFILE', '执行器 profile 必须是项目内文件路径')
    profile = checked_path(root / profile_value, parent=root)
    if not profile.is_file():
        raise APIError('ADAPTER_PROFILE_MISSING', '执行器配置尚未准备完成：' + profile.name, 503)
    if selected == 'arclight-native-cy8':
        from adapters.arclight import ArcLightAdapter
        return ArcLightAdapter(root, profile)
    from adapters.f8 import F8Adapter
    return F8Adapter(root, profile)

class Controller:
    def __init__(self, root=ROOT, *, adapter=None, db_path=None, start_worker=True, brain_url='http://127.0.0.1:8766'):
        self.root = Path(root)
        self.store = Store(db_path or self.root / 'data' / 'platform.sqlite', root=self.root)
        self.archive = ResultArchive(self.store, self.root)
        self.catalog = Catalog(self.root)
        self.brain_url = brain_url
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.worker = None
        if adapter is None:
            adapter = configured_adapter(self.root)
        self.adapter = adapter
        self.profile = getattr(adapter, 'profile', {})
        self._physical_adapter = getattr(adapter, 'physical_hardware', None) is True
        if not self._physical_adapter and getattr(adapter, 'test_adapter', None) is not True:
            raise APIError('ADAPTER_CONTRACT_REQUIRED', '执行器必须明确声明物理设备或测试替身，不能默认绕过设备独占')
        required = ['capabilities', 'preflight', 'execute', 'reconcile']
        if self._physical_adapter:
            required += ['with_execution_enabled', 'hardware_ownership', 'build_plan']
        if any(not callable(getattr(adapter, method, None)) for method in required):
            raise APIError('ADAPTER_CONTRACT_REQUIRED', '执行器缺少必需的独占、执行或回执接口')
        self.adapter_id = self.profile.get('id') or ('explicit-test-adapter' if not self._physical_adapter else None)
        if not self.adapter_id:
            raise APIError('ADAPTER_ID_REQUIRED', '物理执行器必须有稳定的配置编号')
        self.adapter_profile_hash = self._adapter_fingerprint()
        self._ownership_cache = None
        self.experience = ExperienceLedger(parameter_bounds=self._bounds())
        with self.store.transaction() as rt:
            self.experience.ensure_schema(rt)
        self.history = self.catalog.history()
        self.records = self.history['records']
        self._register_imports()
        self._recover_interrupted()
        if start_worker:
            self.worker = threading.Thread(target=self._loop, name='qcal-experiment-owner', daemon=True)
            self.worker.start()

    def close(self):
        self.stop_event.set()
        self.wake.set()
        if self.worker:
            self.worker.join(timeout=25)

    def _register_imports(self):
        with self.store.transaction() as rt:
            for record in self.records:
                for artifact in record.get('result', {}).get('artifacts', []):
                    self._artifact(rt, artifact, verify=False)

    def _artifact(self, rt, artifact, verify=True):
        path_value = artifact.get('localPath') or artifact.get('sourcePath')
        if not path_value:
            registered = rt.db.execute('SELECT path,sha256 FROM platform_artifacts WHERE id=?', (artifact.get('id'),)).fetchone()
            if not registered or registered['sha256'] != artifact.get('sha256'):
                raise APIError('MISSING_ARTIFACT', '产物未提供实际文件路径且尚未登记')
            path_value = registered['path']
        path = checked_path(path_value)
        permitted = path.is_relative_to(self.root.resolve()) or str(path).replace('\\', '/').lower().startswith('d:/总体-8号机测试/')
        if not permitted:
            raise APIError('ARTIFACT_OUTSIDE_SOURCE', '产物不属于平台或已登记实验档案', 403)
        if not path.is_file():
            raise APIError('MISSING_ARTIFACT', f'产物不存在：{path.name}')
        expected = artifact.get('sha256')
        if not expected or (verify and file_hash(path) != expected):
            raise APIError('ARTIFACT_HASH_MISMATCH', '产物哈希不一致')
        old = rt.db.execute('SELECT sha256 FROM platform_artifacts WHERE id=?', (artifact['id'],)).fetchone()
        if old and old[0] != expected:
            raise APIError('ARTIFACT_ID_CONFLICT', '产物编号对应不同内容')
        clean = {k: v for k, v in artifact.items() if k != 'localPath'}
        clean['url'] = '/api/artifacts/' + artifact['id']
        rt.db.execute('INSERT OR REPLACE INTO platform_artifacts VALUES(?,?,?,?)',
                      (artifact['id'], str(path), expected, dumps(clean)))
        return clean

    def _recover_interrupted(self):
        with self.store.transaction() as rt:
            for wf in self.store.workflows(rt):
                diagnostic = wf.get('rbDiagnostic') or {}
                if diagnostic.get('state') in ('queued', 'running'):
                    diagnostic.update(state='interrupted', finishedAt=now(), resultStatus='unverified')
                    wf['workflowComplete'] = False
                    if wf['status'] == 'queued':
                        wf.update(status='paused', engineState='paused')
                        self.store.save(rt, wf, 'rb_diagnostic_interrupted', {'message': '服务重启：单次诊断许可已关闭，未自动重发'})
                        continue
                    if wf['status'] != 'running':
                        # Preflight/ownership/reconciliation may have put the
                        # run in attention while its one-shot permit was open.
                        # Persist closure without clearing existing quarantine.
                        self.store.save(rt, wf, 'rb_diagnostic_interrupted', {'message': '服务重启：关闭未结束的单次诊断许可，保留原停机及隔离状态'})
                        continue
                if wf['status'] != 'running':
                    continue
                if wf['mode'] == 'replay':
                    wf['status'], wf['engineState'] = 'paused', 'replay_interrupted'
                else:
                    wf['status'], wf['engineState'] = 'attention', 'needs_reconciliation'
                    rt.db.execute("UPDATE platform_leases SET state='quarantined' WHERE workflow_id=?", (wf['id'],))
                    rt.db.execute("UPDATE platform_effects SET state='unknown' WHERE workflow_id=? AND state='pending'", (wf['id'],))
                self.store.save(rt, wf, 'reconciliation_required', {'message': '服务重启：保留执行记录，未自动重发仪器命令'})

    def get(self, workflow_id, rt=None):
        if rt is None:
            with self.store.connect() as conn:
                return self.get(workflow_id, conn)
        wf = self.store.get(rt, workflow_id)
        if wf is None:
            raise APIError('NOT_FOUND', '任务不存在', 404)
        return self._with_timing(rt, wf)

    def list(self):
        with self.store.connect() as rt:
            return [self._with_timing(rt, wf) for wf in self.store.workflows(rt)]

    def snapshot(self):
        """Read the event cursor and timing-enriched workflows without writes."""
        with self.store.lock, self.store.connect() as rt:
            seq = rt.db.execute('SELECT coalesce(max(seq),0) FROM events').fetchone()[0]
            return {'version': 1, 'seq': seq,
                    'workflows': [self._with_timing(rt, wf) for wf in self.store.workflows(rt)]}

    @staticmethod
    def _timestamp(value):
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return parsed if parsed.tzinfo is not None else None
        except ValueError:
            return None

    def _with_timing(self, rt, wf):
        # Legacy records are enriched in memory only. Task creation is never
        # used as a substitute for a real acquisition attempt's start.
        start = wf.get('firstAcquisitionAt') if wf.get('mode') == 'real' else None
        if self._timestamp(start) is None:
            starts = []
            if wf.get('mode') == 'real':
                for row in rt.db.execute('SELECT payload FROM platform_attempts WHERE workflow_id=?', (wf['id'],)):
                    attempt = json.loads(row[0])
                    value = attempt.get('startedAt')
                    if attempt.get('mode') == 'real' and self._timestamp(value) is not None:
                        starts.append(value)
            start = min(starts, key=self._timestamp) if starts else None
        finish = wf.get('finishedAt') if wf.get('status') in TERMINAL else None
        if wf.get('status') in TERMINAL and self._timestamp(finish) is None:
            # Prefer the immutable terminal event for legacy tasks; updatedAt
            # is a stable fallback when an older event ledger is unavailable.
            for row in rt.db.execute("SELECT event,at,payload FROM events WHERE run_id=? AND event IN ('completed','aborted','model_decision') ORDER BY seq DESC", (wf['id'],)):
                if row['event'] == 'model_decision' and json.loads(row['payload']).get('action') != 'stop':
                    continue
                finish = datetime.fromtimestamp(row['at'], timezone.utc).isoformat()
                break
            if self._timestamp(finish) is None:
                finish = wf.get('updatedAt') if self._timestamp(wf.get('updatedAt')) is not None else None
        wf['firstAcquisitionAt'], wf['finishedAt'] = start, finish
        end = self._timestamp(finish) if wf.get('status') in TERMINAL else self._timestamp(now())
        wf['elapsed'] = max(0.0, (end - self._timestamp(start)).total_seconds()) if start and end else 0
        return wf

    @staticmethod
    def _finish_timing(wf):
        if wf.get('status') in TERMINAL and not wf.get('finishedAt'):
            wf['finishedAt'] = now()

    def _node(self, wf, node_id=None):
        node_id = node_id or wf.get('currentNodeId')
        node = next((n for n in wf['nodes'] if n['id'] == node_id), None)
        if node is None:
            raise APIError('NODE_NOT_FOUND', '任务中不存在该节点', 404)
        return node

    def create(self, data):
        mode = data.get('mode', 'replay')
        if mode not in ('replay', 'real'):
            raise APIError('INVALID_MODE', '请选择历史回放或真机测量')
        name = str(data.get('name', '')).strip()
        if not 1 <= len(name) <= 200:
            raise APIError('INVALID_NAME', '任务名称长度须为 1–200')
        resources = {r['id']: r for r in self.history['resources']}
        selected = data.get('resourceIds')
        if not selected:
            target = str(data.get('target', '')).strip()
            selected = [target] if target in resources else []
        if not isinstance(selected, list) or not selected or len(set(selected)) != len(selected) or any(r not in resources for r in selected):
            raise APIError('UNKNOWN_RESOURCE', '请选择已核实映射的 Q11 或 Q18；资源编号不自动去除前导零')
        supported_resources = self.adapter.capabilities().get('resources')
        if mode == 'real' and self._physical_adapter and isinstance(supported_resources, list) and any(r not in supported_resources for r in selected):
            raise APIError('UNSUPPORTED_RESOURCE', '当前执行器尚未绑定所选对象的真机映射；请选择已支持资源')
        experiment = data.get('experiment', 'single')
        if experiment not in ('single', 'two'):
            raise APIError('INVALID_EXPERIMENT', '未知实验类型')
        policy = data.get('policy', 'threshold')
        if policy not in ('strict', 'threshold', 'autopilot'):
            raise APIError('INVALID_POLICY', '未知运行策略')
        coverage = data.get('coverage', 'full' if mode == 'real' else 'main')
        if coverage not in ('main', 'full', 'working_point'):
            raise APIError('INVALID_COVERAGE', '测量范围必须是 main、full 或 working_point')
        seed = None
        if coverage == 'working_point':
            if mode != 'real' or experiment != 'single' or selected != ['Q11']:
                raise APIError('WORKING_POINT_SCOPE', '复刻工作点仅支持新建 Q11 真机单比特任务', 409)
            seed = build_working_point_seed(self.adapter)
        nodes, edges = self.catalog.nodes([resources[r] for r in selected], mode, self.adapter.capabilities(), experiment, coverage)
        if seed:
            for node in nodes:
                node['parameters'] = deepcopy(seed['parameters'])
                node['parameterVersions'] = {key: 0 for key in seed['parameters']}
        shot_budget = (30000000 if mode == 'real' and coverage in ('full', 'working_point') else 8000000) * len(selected)
        wf = {'id': ident('wf'), 'revision': 0, 'mode': mode, 'name': name, 'machine': '八号机',
              'target': '、'.join(selected), 'resourceIds': selected, 'qubits': len(selected),
              'experiment': experiment, 'coverage': coverage, 'type': data.get('type', 'adaptive'), 'policy': policy,
              'scenario': 'normal', 'status': 'draft', 'engineState': 'draft', 'progress': 0,
              'owner': '现场操作员', 'ownerId': 'local-operator', 'createdAt': now(),
              'elapsed': 0, 'firstAcquisitionAt': None, 'finishedAt': None,
              'iterations': 0, 'approved': False, 'events': [], 'nodes': nodes, 'edges': edges,
              'blueprintVersion': WORKING_POINT_VERSION if seed else VERSION, 'gates': deepcopy(GATES), 'gatesVersion': 'diagnostic-gates/1',
              'adapterId': self.adapter_id, 'adapterProfileHash': self.adapter_profile_hash,
              'maxAttemptsPerNode': 3, 'maxWorkflowAttempts': 40, 'hardwareConfirmed': False,
              'maxTotalShots': shot_budget, 'reservedShots': 0,
              'budget': {'maxTotalShots': shot_budget, 'maxAttemptsPerNode': 3, 'maxWorkflowAttempts': 40,
                         'resourceCount': len(selected), 'basis': 'working-point-pulse-30M-per-resource' if seed else 'full-single-qubit-30M-per-resource' if coverage == 'full' else 'main-8M-per-resource'},
              'cancelRequested': None, 'processedRecordIds': [], 'workflowComplete': False}
        if seed:
            wf['seedReference'] = seed
            wf['completionScope'] = {'selectedNodeCount': len(nodes), 'omittedProcedures': list(WORKING_POINT_OMITTED),
                'full13Fresh': False, 'meaning': '仅指所选10项脉冲测量完成本轮独立验收；不代表原13项全流程均已新测。'}
        with self.store.transaction() as rt:
            for rule in self.experience.published_rules(rt):
                scope = rule['scope']
                for node in nodes:
                    if scope.get('resourceId') == node['resourceId'] and scope.get('ownerId') == node['ownerId'] and scope.get('procedureId', node['procedureId']) == node['procedureId']:
                        node['rules'].append(deepcopy(rule))
            for node in nodes:
                reads, writes = PARAMETERS.get(node['procedureId'], ([], []))
                prefix = wf['id'] + ':' + node['resourceId'] + ':'
                norm = lambda k: prefix + PARAMETER_ALIASES.get(k, k)
                rt.register(StepSpec(wf['id'] + '/' + node['id'],
                                     depends_on=tuple(wf['id'] + '/' + n for n in node['dependsOn']),
                                     reads=tuple(norm(k) for k in reads), writes=tuple(norm(k) for k in writes),
                                     resources=(self.profile.get('instrument', {}).get('resourceGroup', 'CY8_SHARED_BUS1'),), budget=Budget(shots=500, max_attempts=3, timeout_s=3600, max_total_shots=shot_budget),
                                     criteria=deepcopy(GATES.get(node['procedureId'], {}))))
            self.store.save(rt, wf, 'created', {'message': ('历史回放' if mode == 'replay' else '真机测量') + '任务已创建'})
        return wf

    def _command(self, rt, wf, payload, kind):
        op = payload.get('operationId')
        if not isinstance(op, str) or not 1 <= len(op) <= 256:
            raise APIError('OPERATION_ID_REQUIRED', '写操作必须包含唯一 operationId')
        request_hash = digest({'workflowId': wf['id'], 'kind': kind, 'body': payload})
        previous = rt.db.execute('SELECT * FROM platform_commands WHERE id=?', (op,)).fetchone()
        if previous:
            if previous['request_hash'] != request_hash:
                raise APIError('IDEMPOTENCY_CONFLICT', '同一操作编号不能提交不同请求', 409)
            return json.loads(previous['response']), request_hash
        if payload.get('expectedRevision') != wf['revision']:
            raise APIError('STALE_REVISION', '任务已更新，请刷新后按当前状态操作', 409)
        if payload.get('nodeId'):
            node = self._node(wf, payload['nodeId'])
            if payload.get('attemptId') and payload['attemptId'] != node.get('latestAttemptId'):
                raise APIError('STALE_ATTEMPT', '该操作对应的尝试已不是当前尝试', 409)
        return None, request_hash

    def _rb_diagnostic_proof(self, rt, wf):
        self._assert_adapter_binding(wf)
        self._assert_supplemental_snapshot(wf)
        target = next((n for n in wf['nodes'] if n['procedureId'] == 'sq_rb'), None)
        rb_require(target is not None, 'RB_DIAGNOSTIC_SCOPE', '任务没有原登记RB节点')
        parameters, versions = self._parameters(rt, wf, target)
        kind = type(self.adapter)
        proof = rb_diagnostic_proof(self.root, rt.db, wf, parameters, versions,
            implementation_id=kind.__module__ + '.' + kind.__qualname__)
        return proof, target, parameters, versions

    def _rb_diagnostic_admission(self, rt, wf):
        rb_require(wf['status'] in ('paused', 'attention') and wf.get('engineState') not in ('deciding', 'decision_queued')
                   and not wf.get('cancelRequested'), 'RB_DIAGNOSTIC_CHECKPOINT', '请在无活动采集和决策的检查点准备RB诊断')
        group = self.profile.get('instrument', {}).get('resourceGroup', 'CY8_SHARED_BUS1')
        rb_require(not rt.db.execute('SELECT 1 FROM platform_leases WHERE workflow_id=? OR resource=?', (wf['id'], group)).fetchone()
                   and not rt.db.execute("SELECT 1 FROM platform_effects WHERE state IN ('pending','unknown')").fetchone(),
                   'RECONCILIATION_REQUIRED', '采集占用或未知回执尚未清空')
        old = wf.get('rbDiagnostic') or {}
        rb_require(old.get('state') not in ('queued', 'running'), 'RB_DIAGNOSTIC_ACTIVE', '已有单次RB诊断许可')
        proof, target, parameters, versions = self._rb_diagnostic_proof(rt, wf)
        rb_require(target.get('engineState') != 'verified', 'RB_DIAGNOSTIC_ALREADY_VERIFIED', '当前RB已经独立验证，不重复创建诊断许可')
        admit_backtrack(wf, target)
        budget = self._validate_plan(wf, target, parameters, versions, 'rb-diagnostic-inspection', joint_budget=True, rt=rt)
        return proof, budget

    def inspect_rb_diagnostic(self, workflow_id):
        with read_chat_snapshot(self.store.path) as db:
            rt = types.SimpleNamespace(db=db, get_parameters=lambda: {
                r['name']: {'value': json.loads(r['value']), 'revision': r['revision']}
                for r in db.execute('SELECT * FROM parameters')})
            wf = self.get(workflow_id, rt)
            output = {'schema': 'qcal-rb-diagnostic-inspection/1', 'workflowId': workflow_id,
                      'revision': wf['revision'], 'policyVersion': RB_DIAGNOSTIC_VERSION,
                      'eligible': False, 'reasons': [], 'marker': deepcopy(wf.get('rbDiagnostic'))}
            try:
                proof, budget = self._rb_diagnostic_admission(rt, wf)
                output.update(eligible=True, budgetLookahead=budget, **{k: proof[k] for k in
                    ('sourceNodeId', 'sourceAttemptId', 'targetNodeId', 'sourceProofSha256', 'physicalParameterVersions')})
            except APIError as exc:
                output['reasons'] = [{'code': exc.code, 'message': exc.message}]
            return output

    def _queue_rb_diagnostic(self, rt, wf, payload):
        rb_require(set(payload) == {'action', 'operationId', 'expectedRevision', 'nodeId', 'attemptId', 'policyVersion', 'sourceProofSha256'}
                   and payload.get('policyVersion') == RB_DIAGNOSTIC_VERSION,
                   'RB_DIAGNOSTIC_CONTRACT', 'RB诊断请求须使用固定版本及完整来源字段')
        proof, budget = self._rb_diagnostic_admission(rt, wf)
        rb_require(payload.get('nodeId') == proof['sourceNodeId'] and payload.get('attemptId') == proof['sourceAttemptId']
                   and payload.get('sourceProofSha256') == proof['sourceProofSha256'],
                   'RB_DIAGNOSTIC_SOURCE_CHANGED', '来源或参数已变化，请重新检查诊断条件')
        if wf.get('rbDiagnostic'):
            wf.setdefault('rbDiagnosticHistory', []).append(deepcopy(wf['rbDiagnostic']))
        target = self._node(wf, proof['targetNodeId'])
        wf['rbDiagnostic'] = {**proof, 'id': ident('rb-diagnostic'), 'commandOperationId': payload['operationId'],
            'state': 'queued', 'attemptId': None, 'targetAttemptBefore': target['attempt'],
            'requestedAt': now(), 'budgetLookahead': budget, 'publication': 'disabled_diagnostic_only'}
        wf.update(status='queued', engineState='diagnostic_queued', currentNodeId=target['id'], workflowComplete=False)
        self._record_decision(rt, wf, proof['sourceNodeId'], {'action': 'queue_rb_diagnostic',
            'reason': '保留CPMG未通过与原图依赖，仅授权一套RB首测及独立复测；不发布完整校准',
            'evidenceIds': [], 'ruleId': RB_DIAGNOSTIC_VERSION, 'diagnosticId': wf['rbDiagnostic']['id'],
            'sourceAttemptId': proof['sourceAttemptId'], 'sourceProofSha256': proof['sourceProofSha256'],
            'budgetLookahead': budget}, 'operator')

    def _assert_rb_diagnostic_dispatch(self, rt, wf, node, branch_id, *, attempt_id=None, allow_cancel=False):
        marker = wf.get('rbDiagnostic') or {}
        rb_require(marker.get('id') == branch_id and marker.get('policyVersion') == RB_DIAGNOSTIC_VERSION
                   and marker.get('targetNodeId') == node['id'] and (allow_cancel or not wf.get('cancelRequested')),
                   'RB_DIAGNOSTIC_SCOPE', '单次诊断许可身份或状态不一致')
        if attempt_id is None:
            rb_require(marker.get('state') == 'queued' and marker.get('attemptId') is None
                       and node['attempt'] == marker.get('targetAttemptBefore'),
                       'RB_DIAGNOSTIC_CONSUMED', '本次诊断许可已消费或被改变')
            admit_backtrack(wf, node)
        else:
            rb_require(marker.get('state') == 'running' and marker.get('attemptId') == attempt_id
                       and node.get('latestAttemptId') == attempt_id,
                       'RB_DIAGNOSTIC_CONSUMED', '复测不属于本次单次诊断许可')
        proof, _, _, _ = self._rb_diagnostic_proof(rt, wf)
        rb_require(proof['sourceProofSha256'] == marker.get('sourceProofSha256'),
                   'RB_DIAGNOSTIC_SOURCE_CHANGED', '参数、图、源码或来源已改变，单次诊断许可失效')
        return marker

    def _run_rb_diagnostic(self, workflow_id, branch_id):
        try:
            wf = self.get(workflow_id)
            marker = wf['rbDiagnostic']
            self._execute_node(workflow_id, marker['targetNodeId'], diagnostic_branch_id=branch_id)
        except APIError as exc:
            with self.store.transaction() as rt:
                current = self.get(workflow_id, rt)
                marker = current.get('rbDiagnostic') or {}
                if marker.get('id') == branch_id:
                    stopping = (current['status'] in ('paused', 'aborted') or current.get('cancelRequested')
                                or current.get('engineState') == 'needs_reconciliation')
                    marker.update(state='interrupted' if stopping else 'invalidated', errorCode=exc.code)
                    if not stopping:
                        current.update(status='attention', engineState='diagnostic_invalidated')
                    self.store.save(rt, current, 'rb_diagnostic_interrupted' if stopping else 'rb_diagnostic_invalidated',
                                    {'message': exc.message, 'errorCode': exc.code})
        finally:
            with self.store.transaction() as rt:
                current = self.get(workflow_id, rt)
                marker = current.get('rbDiagnostic') or {}
                if marker.get('id') != branch_id:
                    return
                node = self._node(current, marker['targetNodeId'])
                uncertain = self._has_lease(rt, workflow_id) or rt.db.execute(
                    "SELECT 1 FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')", (workflow_id,)).fetchone()
                passed = (node.get('latestAttemptId') == marker.get('attemptId') and node.get('engineState') == 'verified'
                          and node.get('result', {}).get('quality', {}).get('status') == 'passed')
                interrupted = (current['status'] in ('paused', 'aborted') or uncertain or current.get('cancelRequested')
                               or current.get('engineState') == 'needs_reconciliation')
                invalidated = marker.get('state') == 'invalidated'
                marker.update(state='interrupted' if interrupted else 'invalidated' if invalidated else 'completed' if passed else 'failed', finishedAt=now(),
                              resultStatus='passed' if passed else node.get('result', {}).get('quality', {}).get('status', 'unverified'))
                current['workflowComplete'] = False
                if not interrupted:
                    current.update(status='attention', engineState='diagnostic_invalidated' if invalidated else 'diagnostic_complete' if passed else 'diagnostic_failed')
                current['resultSummary'] = {'verifiedNodes': sum(n['engineState'] == 'verified' for n in current['nodes']),
                    'totalNodes': len(current['nodes']), 'coverage': current['coverage'], 'workflowComplete': False,
                    'meaning': 'RB诊断已结束；CPMG原结果与原流程依赖保留，原10项流程尚未全部通过'}
                self._finish_timing(current)
                self.store.save(rt, current, 'rb_diagnostic_finished', {'message': current['resultSummary']['meaning'],
                    'diagnosticId': branch_id, 'attemptId': marker.get('attemptId'), 'state': marker['state']})

    @staticmethod
    def _rb_diagnostic_candidate_contract(result, stage):
        if result.get('candidateParameters', {}) != {}:
            result['quality']['status'] = 'failed'
            result['quality']['reasons'].append('RB诊断回执必须没有待提交校准参数；已保留采集但拒绝该候选')
            result['diagnosticContract'] = {'accepted': False, 'code': 'RB_DIAGNOSTIC_CANDIDATE_NOT_EMPTY',
                'stage': stage, 'rawCandidateParameters': deepcopy(result.get('candidateParameters'))}

    def action(self, workflow_id, payload):
        with self.store.transaction() as rt:
            wf = self.get(workflow_id, rt)
            existing, hash_value = self._command(rt, wf, payload, 'action')
            if existing:
                return existing
            action = payload.get('action')
            if wf['status'] in TERMINAL:
                raise APIError('TERMINAL_RUN', '已结束任务不能再次下发；请创建新任务', 409)
            diagnostic = wf.get('rbDiagnostic') or {}
            if diagnostic.get('state') in ('queued', 'running') and action not in ('pause', 'abort', 'reconcile'):
                raise APIError('RB_DIAGNOSTIC_ACTIVE', '单次RB诊断期间仅允许暂停、终止或回执核查', 409)
            if (diagnostic and wf.get('currentNodeId') == diagnostic.get('targetNodeId')
                    and action in ('resume', 'retry', 'approve', 'decide', 'confirm_hardware')):
                raise APIError('RB_DIAGNOSTIC_CLOSED', 'RB诊断不通过普通继续操作推进；请显式重新检查诊断条件或回退原前置步骤', 409)
            if action in ('retry', 'backtrack'):
                target_id = payload.get('targetNodeId') if action == 'backtrack' else payload.get('nodeId')
                target = self._node(wf, target_id)
                if target['procedureId'] == 'sq_rb' and any(self._node(wf, d)['engineState'] != 'verified' for d in target['dependsOn']):
                    raise APIError('PREREQUISITES_MISSING', '普通RB操作必须满足原图全部依赖；诊断须使用独立版本化命令', 409)
            if action == 'start':
                if wf['status'] != 'draft':
                    raise APIError('INVALID_STATE', '仅草稿可开始', 409)
                wf['status'], wf['engineState'] = 'queued', 'queued'
            elif action == 'queue_rb_diagnostic':
                self._queue_rb_diagnostic(rt, wf, payload)
            elif action == 'pause':
                if wf['status'] == 'running':
                    wf['cancelRequested'], wf['engineState'] = 'pause', 'pausing_at_checkpoint'
                elif wf['status'] == 'queued' or (diagnostic.get('state') == 'queued' and wf['status'] == 'attention'):
                    wf['status'], wf['engineState'] = 'paused', 'paused'
                else:
                    raise APIError('INVALID_STATE', '当前状态不能暂停', 409)
            elif action == 'abort':
                if wf['status'] == 'running':
                    wf['cancelRequested'], wf['engineState'] = 'abort', 'stopping_at_checkpoint'
                elif self._has_lease(rt, wf['id']):
                    raise APIError('RECONCILIATION_REQUIRED', '设备结果尚待核查，不能宣称已终止', 409)
                else:
                    wf['status'], wf['engineState'] = 'aborted', 'aborted'
            elif action in ('resume', 'retry'):
                if wf['status'] not in ('paused', 'attention', 'failed'):
                    raise APIError('INVALID_STATE', '当前状态不能继续或重试', 409)
                if self._has_lease(rt, wf['id']):
                    raise APIError('RECONCILIATION_REQUIRED', '上次设备操作状态未知，先核查回执', 409)
                if action == 'retry':
                    node = self._node(wf, payload.get('nodeId'))
                    if node['attempt'] >= wf['maxAttemptsPerNode']:
                        raise APIError('ATTEMPT_BUDGET', '该节点已达到本轮重试预算', 409)
                    node['status'], node['engineState'] = 'pending', 'retry_queued'
                wf['status'], wf['engineState'], wf['cancelRequested'] = 'queued', 'queued', None
            elif action == 'decide':
                legacy_decision_error = self._legacy_decision_error(rt, wf, payload.get('nodeId'))
                if wf['mode'] != 'real' or wf['policy'] != 'autopilot' or (wf['engineState'] not in ('quality_attention', 'waiting_model', 'budget_updated') and not legacy_decision_error):
                    raise APIError('INVALID_STATE', '仅自主策略下待处理的质量结果或模型等待状态可重新请求决策', 409)
                if self._has_lease(rt, wf['id']) or rt.db.execute("SELECT 1 FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')", (wf['id'],)).fetchone():
                    raise APIError('RECONCILIATION_REQUIRED', '采集状态尚待核查，不能请求新决策', 409)
                node = self._node(wf, payload.get('nodeId'))
                if not node.get('result') or node['result'].get('stale'):
                    raise APIError('CURRENT_EVIDENCE_REQUIRED', '需要当前未失效的采集结果才能重新决策', 409)
                if wf['engineState'] == 'budget_updated' and (node['id'] != wf.get('currentNodeId')
                        or node['result'].get('quality', {}).get('status') not in ('failed', 'unverified')):
                    raise APIError('CURRENT_EVIDENCE_REQUIRED', '预算调整后仅可基于当前步骤未通过或待验证的结果重新决策', 409)
                wf['currentNodeId'] = node['id']
                wf['status'], wf['engineState'] = 'queued', 'decision_queued'
                node.update(status='attention', engineState='decision_queued', detail='等待基于当前实测结果重新请求模型决策')
                if legacy_decision_error:
                    self.store.save(rt, wf, 'legacy_model_decision_recovery', {
                        'message': '旧版本模型证据引用错误已进入重新决策；原始采集与尝试历史保持不变',
                        'nodeId': node['id'], 'attemptId': node.get('latestAttemptId'), 'errorCode': 'BAD_EVIDENCE'})
            elif action == 'approve':
                node = self._node(wf, payload.get('nodeId'))
                if node.get('result', {}).get('quality', {}).get('status') != 'passed' or node.get('result', {}).get('stale'):
                    raise APIError('GATE_NOT_PASSED', '确认操作不能将未通过或未验证的指标变为合格', 409)
                node['status'], node['engineState'] = 'success', 'verified'
                wf['status'], wf['engineState'], wf['approved'] = 'queued', 'queued', True
            elif action == 'backtrack':
                if wf['status'] == 'running' or self._has_lease(rt, wf['id']):
                    raise APIError('RESOURCE_BUSY', '等待当前采集结束后再回退', 409)
                target = payload.get('targetNodeId')
                target_node = self._node(wf, target)
                admit_backtrack(wf, target_node)
                if self._physical_adapter:
                    self._assert_adapter_binding(wf)
                    parameters, versions = self._parameters(rt, wf, target_node)
                    trial, _ = self._readout_retry_parameters(rt, wf, target_node, parameters, versions, {})
                    self._validate_plan(wf, target_node, trial, versions,
                        ident('operator-backtrack-plan'), joint_budget=True, rt=rt)
                origin = payload.get('nodeId') or wf.get('currentNodeId') or target
                affected = self._invalidate(wf, target, include_target=True)
                wf['currentNodeId'], wf['status'], wf['engineState'] = target, 'queued', 'backtrack_queued'
                self._record_decision(rt, wf, origin, {'action': 'backtrack', 'reason': '操作员请求回到前置步骤', 'evidenceIds': [],
                                                     'targetNodeId': target, 'invalidatedNodes': affected}, 'operator')
            elif action == 'confirm_hardware':
                if wf['mode'] != 'real' or wf['engineState'] != 'waiting_hardware':
                    raise APIError('INVALID_STATE', '当前任务没有等待硬件交接', 409)
                ownership = self.hardware_ownership(refresh=True)
                if not ownership['verified'] or ownership['owners']:
                    raise APIError('DEVICE_IN_USE', '设备仍有外部连接或尚不能核实，请先在现用程序中释放连接', 409)
                wf['hardwareConfirmed'], wf['hardwareConfirmedAt'] = True, now()
                wf['status'], wf['engineState'] = 'queued', 'queued'
            elif action == 'reconcile':
                if wf['engineState'] != 'needs_reconciliation':
                    raise APIError('INVALID_STATE', '当前任务不需要执行回执核查', 409)
                unknown = list(rt.db.execute("SELECT * FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')", (workflow_id,)))
                remaining = []
                for effect in unknown:
                    receipt = self.adapter.reconcile(effect['operation_id'])
                    if receipt.get('status') == 'completed' and isinstance(receipt.get('result'), dict):
                        recovered = deepcopy(receipt['result'])
                        recovered['receiptQuality'] = recovered.get('quality')
                        recovered['quality'] = {'status': 'unverified', 'reasons': ['已找回采集回执，尚未重新完成本轮独立复测和冻结规则验收']}
                        recovered['artifacts'] = [self._artifact(rt, a) for a in recovered.get('artifacts', [])]
                        rt.db.execute("UPDATE platform_effects SET state='completed',result=? WHERE operation_id=?", (dumps(recovered), effect['operation_id']))
                        record_effect(rt.db, effect['operation_id'], self.root)
                        row = rt.db.execute('SELECT payload FROM platform_attempts WHERE id=?', (effect['attempt_id'],)).fetchone()
                        if row:
                            attempt = json.loads(row[0])
                            selection = self._completed_selection_for_reconciliation(rt, workflow_id, attempt, effect)
                            if selection is not None:
                                recovered['selectionResult'] = selection
                            attempt.update(result=recovered, state='reconciled_unverified', endedAt=now())
                            self.store.attempt(rt, workflow_id, attempt)
                            node = self._node(wf, attempt['nodeId'])
                            node.update(result=recovered, status='attention', engineState='reconciled_unverified',
                                        detail='已找回完成回执；尚未重新满足本轮独立验收')
                    elif receipt.get('status') == 'not_started':
                        rt.db.execute("UPDATE platform_effects SET state='not_started',result=? WHERE operation_id=?", (dumps(receipt), effect['operation_id']))
                    elif receipt.get('status') in ('cancelled', 'failed_after_shutdown') and receipt.get('shutdownConfirmed') is True:
                        receipt['partialArtifacts'] = [self._artifact(rt, artifact) for artifact in receipt.get('partialArtifacts', [])]
                        rt.db.execute('UPDATE platform_effects SET state=?,result=? WHERE operation_id=?', (receipt['status'], dumps(receipt), effect['operation_id']))
                        record_effect(rt.db, effect['operation_id'], self.root)
                        row = rt.db.execute('SELECT payload FROM platform_attempts WHERE id=?', (effect['attempt_id'],)).fetchone()
                        if row:
                            attempt = json.loads(row[0])
                            partial_result = self._partial_result(receipt['partialArtifacts'])
                            selection = self._completed_selection_for_reconciliation(rt, workflow_id, attempt, effect)
                            if selection is not None:
                                partial_result['selectionResult'] = selection
                            attempt.update(state=receipt['status'], endedAt=now(), result=partial_result)
                            self.store.attempt(rt, workflow_id, attempt)
                            self._node(wf, attempt['nodeId']).update(result=attempt['result'], status='attention', engineState=receipt['status'], detail='已核查采集停止，保留未验收的部分数据')
                    else:
                        remaining.append(effect['operation_id'])
                if remaining:
                    wf['status'], wf['engineState'] = 'attention', 'needs_reconciliation'
                else:
                    rt.db.execute('DELETE FROM platform_leases WHERE workflow_id=?', (workflow_id,))
                    if wf.get('cancelRequested'):
                        self._finish_cancel_locked(rt, wf)
                    else:
                        wf['status'], wf['engineState'] = 'paused', 'reconciled'
            else:
                raise APIError('INVALID_ACTION', '未知操作')
            if diagnostic.get('state') in ('queued', 'running') and wf['status'] in ('paused', 'aborted'):
                diagnostic.update(state='interrupted', finishedAt=now(), resultStatus='unverified')
            self._finish_timing(wf)
            self.store.save(rt, wf, action, {'message': '操作：' + action})
            rt.db.execute('INSERT INTO platform_commands VALUES(?,?,?)', (payload['operationId'], hash_value, dumps(wf)))
        self.wake.set()
        return wf

    def withdraw_readout_candidate(self, workflow_id, payload):
        """Explicit operator restoration, never an experimental calibration commit."""
        with self.store.transaction() as rt:
            wf = self.get(workflow_id, rt)
            existing, request_hash = self._command(rt, wf, payload, 'readout_withdrawal')
            if existing:
                return existing
            if set(payload) != {'operationId', 'expectedRevision', 'revalidationId', 'sourceAttemptId', 'reason'}:
                raise APIError('INVALID_READOUT_WITHDRAWAL', '撤回请求只接受操作编号、版本、重建编号、来源尝试及原因')
            reason = payload.get('reason')
            if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 2000:
                raise APIError('INVALID_READOUT_WITHDRAWAL', '请填写 1–2000 字的撤回原因')
            if wf['mode'] != 'real' or wf['status'] not in ('paused', 'attention') or wf.get('cancelRequested'):
                raise APIError('INVALID_STATE', '只能在真机任务已暂停或待处理的检查点撤回候选', 409)
            if (self._has_lease(rt, workflow_id) or rt.db.execute(
                    "SELECT 1 FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')", (workflow_id,)).fetchone()):
                raise APIError('RECONCILIATION_REQUIRED', '采集租约或未知回执尚未释放，不能撤回读出候选', 409)
            self._assert_adapter_binding(wf)
            markers = [m for m in wf.get('readoutRevalidations', {}).values()
                       if m.get('id') == payload['revalidationId']]
            if (len(markers) != 1 or markers[0].get('state') not in ('active', 'fixed_failed')
                    or markers[0].get('sourceAttemptId') != payload['sourceAttemptId']
                    or markers[0].get('origin') == 'operator_withdrawal'):
                raise APIError('READOUT_WITHDRAWAL_MARKER', '读出重建或原候选来源已变化，不能重复撤回', 409)
            marker = markers[0]
            optimizer = self._node(wf, marker['optimizerNodeId'])
            if (optimizer.get('ownerId') != 's05_readout_optimization' or optimizer.get('procedureId') != 'ro_optimize'
                    or optimizer.get('resourceId') != marker['resourceId']
                    or optimizer['id'] != marker['resourceId'] + ':s05_readout_optimization'):
                raise APIError('READOUT_WITHDRAWAL_OWNER', '读出优化归属不匹配', 409)
            # Verify the immutable owner contract and the complete dependency graph.
            for node in wf['nodes']:
                spec = rt.steps.get(workflow_id + '/' + node['id'])
                reads, writes = PARAMETERS.get(node['procedureId'], ([], []))
                prefix = workflow_id + ':' + node['resourceId'] + ':'
                norm = lambda keys: tuple(prefix + PARAMETER_ALIASES.get(k, k) for k in keys)
                if (spec is None or tuple(spec.depends_on) != tuple(workflow_id + '/' + d for d in node['dependsOn'])
                        or tuple(spec.reads) != norm(reads) or tuple(spec.writes) != norm(writes)
                        or spec.criteria != wf['gates'].get(node['procedureId'], {})):
                    raise APIError('READOUT_WITHDRAWAL_OWNER', '已登记步骤的读写或依赖契约不匹配', 409)
            parameters, versions = self._parameters(rt, wf, {**optimizer, 'parameterOverrides': {}})
            source = restoration_source(rt.db, self.adapter, wf, marker, parameters, versions)
            before = {'values': {k: parameters[k] for k in READOUT_KEYS},
                      'parameterVersions': {k: versions[k] for k in READOUT_KEYS}}
            after = {'values': deepcopy(source['values']),
                     'parameterVersions': {k: versions[k] + 1 for k in READOUT_KEYS}}
            prefix = workflow_id + ':' + marker['resourceId'] + ':'
            for key in READOUT_KEYS:
                # Both readout components get a new version, including an equal
                # frequency: this is a new unvalidated working-point restoration.
                rt.db.execute('INSERT OR REPLACE INTO parameters VALUES(?,?,?)',
                              (prefix + key, dumps(after['values'][key]), after['parameterVersions'][key]))
                optimizer['parameterVersions'][key] = after['parameterVersions'][key]
            rt._invalidate([prefix + k for k in READOUT_KEYS])
            marker.update(state='withdrawn', endedAt=now(), withdrawalOperationId=payload['operationId'])
            self._start_readout_revalidation(rt, wf, optimizer)
            restored = wf['readoutRevalidations'][optimizer['resourceId']]
            restored.update(origin='operator_withdrawal', validationStatus='unverified',
                withdrawalOperationId=payload['operationId'], sourceAttemptId=payload['sourceAttemptId'],
                previousRevalidationId=payload['revalidationId'])
            for node in wf['nodes']:
                if node['id'] in restored['lockedNodeIds']:
                    # Current configuration display only. Historical result and
                    # attempt parameter snapshots retain their acquired values.
                    node.setdefault('parameters', {}).update(after['values'])
                    node.setdefault('parameterVersions', {}).update(after['parameterVersions'])
            # _start_readout_revalidation invalidates the entire pulse chain and
            # fixes subsequent ROopt to the restored value. Never auto-start it.
            wf.update(status='paused', engineState='readout_withdrawal_pending', cancelRequested=None,
                      workflowComplete=False, approved=False)
            record = {'ruleVersion': READOUT_WITHDRAWAL_VERSION, 'operationId': payload['operationId'],
                'source': 'operator_withdrawal', 'validationStatus': 'unverified', 'reason': reason.strip(),
                'createdAt': now(), 'before': before, 'after': after, 'sourceEvidence': source['sourceEvidence'],
                'invalidatedNodes': deepcopy(restored['lockedNodeIds']), 'revalidationId': restored['id'],
                'previousRevalidationId': payload['revalidationId'],
                'meaning': '恢复历史读出参数作为待复测工作点；不恢复任何旧验收，不启动采集。'}
            wf.setdefault('readoutWithdrawals', []).append(deepcopy(record))
            wf['lastReadoutWithdrawal'] = record
            self._record_decision(rt, wf, optimizer['id'], {'action': 'operator_withdrawal',
                'reason': record['meaning'] + ' ' + reason.strip(), 'evidenceIds': [],
                'sourceEvidence': source['sourceEvidence'], 'parameterChanges': {'before': before, 'after': after},
                'invalidatedNodes': record['invalidatedNodes'], 'ruleId': READOUT_WITHDRAWAL_VERSION}, 'operator')
            self.store.save(rt, wf, 'readout_candidate_withdrawn', {**record,
                'message': '已撤回读出候选并恢复原基准；全部受影响校准待复测，保持暂停'})
            rt.db.execute('INSERT INTO platform_commands VALUES(?,?,?)', (payload['operationId'], request_hash, dumps(wf)))
        return wf

    def set_policy(self, workflow_id, payload):
        with self.store.transaction() as rt:
            wf = self.get(workflow_id, rt)
            existing, hash_value = self._command(rt, wf, payload, 'policy')
            if existing:
                return existing
            if payload.get('policy') not in ('strict', 'threshold', 'autopilot'):
                raise APIError('INVALID_POLICY', '未知策略')
            if wf['status'] == 'running':
                raise APIError('INVALID_STATE', '请在采集检查点暂停后更改运行策略', 409)
            wf['policy'] = payload['policy']
            self.store.save(rt, wf, 'policy_changed', {'message': '运行策略更新为 ' + wf['policy']})
            rt.db.execute('INSERT INTO platform_commands VALUES(?,?,?)', (payload['operationId'], hash_value, dumps(wf)))
        return wf

    def set_budget(self, workflow_id, payload):
        ceilings = {'maxTotalShots': 100000000, 'maxAttemptsPerNode': 10, 'maxWorkflowAttempts': 200}
        try:
            dumps(payload)
        except (ValueError, TypeError):
            raise APIError('INVALID_BUDGET', '预算请求必须为有限的 JSON 值') from None
        with self.store.transaction() as rt:
            wf = self.get(workflow_id, rt)
            existing, request_hash = self._command(rt, wf, payload, 'budget')
            if existing:
                return existing
            if wf['mode'] != 'real' or wf['status'] not in ('draft', 'paused', 'attention'):
                raise APIError('INVALID_STATE', '只能在真机任务草稿、暂停或待处理检查点调整预算', 409)
            pending = rt.db.execute("SELECT 1 FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')", (workflow_id,)).fetchone()
            if self._has_lease(rt, workflow_id) or pending:
                raise APIError('RECONCILIATION_REQUIRED', '采集租约或执行回执尚未确认，不能调整预算', 409)
            if set(payload) - {'operationId', 'expectedRevision', 'budget', 'reason'}:
                raise APIError('INVALID_BUDGET', '预算请求包含不允许的字段')
            proposed, reason = payload.get('budget'), payload.get('reason')
            if not isinstance(proposed, dict) or not proposed or set(proposed) - set(ceilings):
                raise APIError('INVALID_BUDGET', '预算仅支持 maxTotalShots、maxAttemptsPerNode、maxWorkflowAttempts')
            if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 2000:
                raise APIError('INVALID_BUDGET_REASON', '请填写 1–2000 字的预算调整原因')
            previous = {key: wf[key] for key in ceilings}
            updated = {**previous, **proposed}
            for key, value in updated.items():
                if type(value) is not int or not previous[key] <= value <= ceilings[key]:
                    raise APIError('INVALID_BUDGET', f'{key} 必须为不小于当前值且不大于 {ceilings[key]} 的整数')
            if updated == previous:
                raise APIError('INVALID_BUDGET', '至少增加一项预算；不会自动扩大任务预算')
            if (updated['maxTotalShots'] < wf.get('reservedShots', 0) or updated['maxWorkflowAttempts'] < wf['iterations']
                    or updated['maxAttemptsPerNode'] < max((n['attempt'] for n in wf['nodes']), default=0)):
                raise APIError('INVALID_BUDGET', '预算不得小于已记账的采集或尝试次数')
            wf.update(updated)
            wf.setdefault('budget', {}).update(updated)
            update = {'operationId': payload['operationId'], 'source': 'operator', 'before': previous,
                      'after': updated, 'reason': reason.strip(), 'ceilings': ceilings, 'updatedAt': now()}
            wf['lastBudgetUpdate'] = deepcopy(update)
            if wf['engineState'] == 'budget_exhausted':
                wf['engineState'] = 'budget_updated'
                if wf.get('currentNodeId'):
                    node = self._node(wf)
                    if node['engineState'] == 'budget_exhausted':
                        node.update(engineState='budget_updated', detail='操作员已调整预算；等待明确继续')
            self.store.save(rt, wf, 'budget_updated', {**update, 'message': '操作员调整任务预算：' + reason.strip() + '；未自动启动采集'})
            rt.db.execute('INSERT INTO platform_commands VALUES(?,?,?)', (payload['operationId'], request_hash, dumps(wf)))
        return wf

    def _loaded_supplemental_module(self):
        # Compile exactly the bytes whose hash is frozen, independent of any
        # older sys.modules cache. This fixed source path is not user input.
        path = Path(__file__).with_name('supplemental_quality.py')
        source = path.read_bytes()
        source_hash = hashlib.sha256(source).hexdigest()
        cached = getattr(self, '_supplemental_loaded_module', None)
        if cached is not None:
            if cached._qcal_source_sha256 != source_hash:
                raise APIError('SUPPLEMENTAL_SOURCE_CHANGED', '补充验收源码已变化；请在检查点重新加载服务，不能混用已加载版本', 409)
            return cached
        module = types.ModuleType('backend._supplemental_quality_' + source_hash[:16])
        module.__file__ = str(path)
        exec(compile(source, str(path), 'exec'), module.__dict__)
        module._qcal_source_sha256 = source_hash
        self._supplemental_loaded_module = module
        return module

    def _supplemental_sources(self):
        module = self._loaded_supplemental_module()
        root = checked_path(getattr(self.adapter, 'root', self.root))
        manifest_name, runtime_name = self.profile.get('sourceManifest'), self.profile.get('runtimeRoot')
        if not isinstance(manifest_name, str) or not isinstance(runtime_name, str):
            raise APIError('SUPPLEMENTAL_SOURCE_UNAVAILABLE', '当前执行器没有可冻结的原生分析源码清单')
        manifest = checked_path(root / manifest_name, parent=root)
        analysis_source = checked_path(root / runtime_name / 'platform_analysis.py', parent=root)
        records = json.loads(manifest.read_text(encoding='utf-8'))
        entries = [entry for entry in records if isinstance(entry, dict) and isinstance(entry.get('path'), str)
                   and entry['path'].replace('\\', '/').endswith('/platform_analysis.py')
                   and checked_path(root / entry['path'], parent=root) == analysis_source]
        if len(entries) != 1 or file_hash(analysis_source) != entries[0].get('sha256'):
            raise APIError('SUPPLEMENTAL_SOURCE_MISMATCH', '原生 platform_analysis.py 不匹配冻结清单')
        return module, {'moduleSha256': module._qcal_source_sha256,
                        'sourceManifestSha256': file_hash(manifest),
                        'nativeAnalysisSha256': entries[0]['sha256']}

    def install_supplemental_acceptance(self, workflow_id, payload):
        with self.store.transaction() as rt:
            wf = self.get(workflow_id, rt)
            existing, request_hash = self._command(rt, wf, payload, 'supplemental_acceptance')
            if existing:
                return existing
            if set(payload) - {'operationId', 'expectedRevision', 'policyVersion', 'reason'}:
                raise APIError('INVALID_SUPPLEMENTAL_POLICY', '补充验收请求包含不允许的字段')
            if wf['mode'] != 'real' or wf['status'] not in ('draft', 'paused', 'attention'):
                raise APIError('INVALID_STATE', '补充验收只能在非终态真机任务的检查点显式安装', 409)
            if self._has_lease(rt, workflow_id) or rt.db.execute("SELECT 1 FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')", (workflow_id,)).fetchone():
                raise APIError('RECONCILIATION_REQUIRED', '存在活动采集或未知回执，不能安装补充验收', 409)
            if wf.get('supplementalAcceptance'):
                raise APIError('SUPPLEMENTAL_ALREADY_INSTALLED', '该任务已经冻结补充验收；不能重复安装或静默替换', 409)
            reason = payload.get('reason')
            if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 2000:
                raise APIError('INVALID_SUPPLEMENTAL_REASON', '请填写 1–2000 字的补充验收安装原因')
            module = self._loaded_supplemental_module()
            if payload.get('policyVersion') != module.POLICY_VERSION:
                raise APIError('INVALID_SUPPLEMENTAL_POLICY', '未知补充验收版本')
            nodes = [n for n in wf['nodes'] if n['procedureId'] in module.SUPPORTED_PROCEDURES]
            if not nodes:
                raise APIError('SUPPLEMENTAL_NOT_APPLICABLE', '当前蓝图没有适用的测量方法')
            for node in nodes:
                if node['attempt'] != 0 or rt.db.execute('SELECT 1 FROM platform_attempts WHERE workflow_id=? AND node_id=?', (workflow_id, node['id'])).fetchone():
                    raise APIError('SUPPLEMENTAL_ALREADY_STARTED', '适用测量已有执行记录，不能追溯改变旧验收：' + node['label'], 409)
            self._assert_adapter_binding(wf)
            module, sources = self._supplemental_sources()
            policy = module.policy_snapshot()
            snapshot = {'policyVersion': module.POLICY_VERSION, 'policySnapshot': policy, 'policyHash': digest(policy),
                        **sources, 'baseGatesVersion': wf['gatesVersion'], 'baseGatesSha256': digest(wf['gates']),
                        'adapterProfileHash': wf['adapterProfileHash'], 'installedAt': now(), 'reason': reason.strip(),
                        'operationId': payload['operationId'], 'source': 'operator',
                        'applicableProcedures': [p for p in module.SUPPORTED_PROCEDURES if any(n['procedureId'] == p for n in nodes)],
                        'applicableNodeIds': [n['id'] for n in nodes]}
            wf['supplementalAcceptance'] = snapshot
            self.store.save(rt, wf, 'supplemental_acceptance_installed', {
                **snapshot, 'message': '操作员显式安装补充验收 ' + module.POLICY_VERSION + '：' + reason.strip() + '；原验收门槛与测量源码不变，未自动启动'})
            rt.db.execute('INSERT INTO platform_commands VALUES(?,?,?)', (payload['operationId'], request_hash, dumps(wf)))
        return wf

    def _assert_supplemental_snapshot(self, wf):
        snapshot = wf.get('supplementalAcceptance')
        if not snapshot:
            return None
        module, sources = self._supplemental_sources()
        nodes = [n for n in wf['nodes'] if n['procedureId'] in module.SUPPORTED_PROCEDURES]
        expected = {'policyVersion': module.POLICY_VERSION, 'policyHash': digest(module.policy_snapshot()),
                    **sources, 'baseGatesVersion': wf['gatesVersion'], 'baseGatesSha256': digest(wf['gates']),
                    'adapterProfileHash': wf['adapterProfileHash'], 'applicableNodeIds': [n['id'] for n in nodes],
                    'applicableProcedures': [p for p in module.SUPPORTED_PROCEDURES if any(n['procedureId'] == p for n in nodes)]}
        if any(snapshot.get(k) != v for k, v in expected.items()) or digest(snapshot.get('policySnapshot')) != snapshot['policyHash']:
            raise APIError('SUPPLEMENTAL_BINDING_MISMATCH', '补充验收快照、源码或原验收绑定已变化；不能静默应用另一套规则', 409)
        return snapshot

    def _supplemental_evidence(self, request, result, snapshot):
        """Read only registered, hash-bound native analysis and acquisition receipts."""
        operation = request['operationId']
        expected_policy = {k: snapshot[k] for k in ('policyVersion', 'policyHash', 'moduleSha256', 'sourceManifestSha256', 'baseGatesSha256')}
        if request.get('supplementalAcceptance') != expected_policy:
            raise APIError('SUPPLEMENTAL_BINDING_MISMATCH', '采集请求未绑定当前显式安装的补充验收')
        root = checked_path(getattr(self.adapter, 'root', self.root))
        operations = checked_path(getattr(self.adapter, 'operations', root / 'data/runs/arclight_operations'), parent=root)
        directory = checked_path(operations / hashlib.sha256(operation.encode()).hexdigest(), parent=operations)
        with self.store.connect() as rt:
            effect = rt.db.execute('SELECT workflow_id,attempt_id,state,request FROM platform_effects WHERE operation_id=?', (operation,)).fetchone()
            if (not effect or effect['state'] != 'completed' or effect['workflow_id'] != request['runId']
                    or effect['attempt_id'] != request['attemptId'] or digest(json.loads(effect['request'])) != digest(request)):
                raise APIError('SUPPLEMENTAL_EVIDENCE_BINDING', '采集回执与本任务请求不符')
            artifacts = []
            for item in result.get('artifacts', []):
                if item.get('kind') != 'raw' and item.get('name') not in ('plan.json', 'platform_analysis.json', 'job.json', 'status.json'):
                    continue
                row = rt.db.execute('SELECT path,sha256,payload FROM platform_artifacts WHERE id=?', (item.get('id'),)).fetchone()
                if not row or row['sha256'] != item.get('sha256'):
                    raise APIError('SUPPLEMENTAL_EVIDENCE_BINDING', '分析产物没有匹配的已登记 SHA256')
                metadata = json.loads(row['payload'])
                artifacts.append((metadata, checked_path(row['path'], parent=directory)))

        def registered(name, parent=None):
            matches = [(a, p) for a, p in artifacts if a.get('name') == name and (parent is None or p.parent == parent)]
            if len(matches) != 1:
                raise APIError('SUPPLEMENTAL_EVIDENCE_MISSING', '缺少唯一已登记产物：' + name)
            return matches[0]

        def read_json(item):
            metadata, path = item
            if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
                raise APIError('SUPPLEMENTAL_EVIDENCE_MISSING', '分析 JSON 缺失或超过大小限制')
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != metadata['sha256']:
                raise APIError('SUPPLEMENTAL_EVIDENCE_HASH', '已登记分析 JSON 的内容已改变')
            def invalid_constant(value):
                raise ValueError('Nonfinite JSON constant: ' + value)
            return json.loads(content.decode('utf-8-sig'), parse_constant=invalid_constant)

        plan_item = registered('plan.json', directory)
        plan = read_json(plan_item)
        identity = {k: request.get(k) for k in ('runId', 'nodeId', 'attemptId', 'operationId', 'resourceId', 'procedureId', 'parameterVersions')}
        if (plan.get('adapter') != 'arclight' or plan.get('compileOnlyEvidence') or plan.get('operationId') != operation
                or plan.get('resourceId') != request['resourceId'] or plan.get('procedureId') != request['procedureId']
                or plan.get('requestIdentity') != identity or result.get('dataKind') != 'real'
                or result.get('provenance', {}).get('freshAcquisition') is not True
                or result.get('provenance', {}).get('operationId') != operation):
            raise APIError('SUPPLEMENTAL_EVIDENCE_BINDING', '原生计划没有绑定本次真实任务、对象和操作')
        actual = plan.get('parameters')
        if (not isinstance(actual, dict) or actual != result.get('parameters')
                or any(actual.get(k) != v for k, v in request['parameters'].items())):
            raise APIError('SUPPLEMENTAL_CONTEXT_MISMATCH', '原生计划与请求、结果的实际参数不一致')
        keys = self.adapter.validation_parameter_keys() if callable(getattr(self.adapter, 'validation_parameter_keys', None)) else CALIBRATION_KEYS
        if any(k not in actual or k not in request['parameterVersions'] for k in keys):
            raise APIError('SUPPLEMENTAL_CONTEXT_MISSING', '缺少完整校准工作点或参数版本')
        analysis_item = registered('platform_analysis.json')
        folder = analysis_item[1].parent
        if not folder.is_relative_to(directory / 'results'):
            raise APIError('SUPPLEMENTAL_EVIDENCE_BINDING', '分析报告不属于本次原生采集目录')
        report, job, state = read_json(analysis_item), read_json(registered('job.json', folder)), read_json(registered('status.json', folder))
        planned_job = plan['jobs'][-1]
        expected_rows = plan['expectedJobRows'][-1]
        execution_fields = ('xy_physical_channel', 'xy_filter_mhz', 'xy_attenuation_db', 'xy_delay_s',
                            'captures', 'acquisitions_per_point', 'randomize', 'order_seed')
        if (job.get('name') != planned_job.get('name') or job.get('target') != request['resourceId']
                or job.get('parameters') != actual or job.get('spec') != planned_job.get('spec')
                or not isinstance(job.get('config'), dict) or job['config'] != planned_job.get('config')
                or any((key in job) != (key in planned_job) or job.get(key) != planned_job.get(key)
                       for key in execution_fields)
                or state.get('job') != job.get('name') or state.get('target') != request['resourceId']
                or state.get('mode') != 'real_hardware' or state.get('state') != 'acquired'
                or state.get('shutdown_ok') is not True or state.get('points_completed') != expected_rows
                or type(expected_rows) is not int or expected_rows <= 0):
            raise APIError('SUPPLEMENTAL_EVIDENCE_BINDING', '原生任务或完整采集回执不匹配')
        if (report.get('analysis', {}).get('procedureId') != request['procedureId']
                or report.get('analysis', {}).get('source_sha256') != snapshot['nativeAnalysisSha256']
                or report.get('analysis') != result.get('analysisQuality') or report.get('metrics') != result.get('metrics')):
            raise APIError('SUPPLEMENTAL_ANALYSIS_BINDING', '分析内容、源码 SHA 或结果指标不匹配原始 JSON')
        raw = []
        for metadata, path in artifacts:
            if metadata.get('kind') != 'raw':
                continue
            before = path.stat()
            actual_hash = file_hash(path) if before.st_size <= 512 * 1024 * 1024 else None
            after = path.stat()
            if actual_hash != metadata['sha256'] or (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                raise APIError('SUPPLEMENTAL_EVIDENCE_HASH', '原始采集文件 SHA 或完整性不匹配')
            raw.append(metadata['sha256'])
        main_raw = registered('raw_scan.npz', folder)[0]['sha256']
        if not raw or report['analysis'].get('raw_sha256') != main_raw or main_raw not in raw:
            raise APIError('SUPPLEMENTAL_EVIDENCE_BINDING', '分析没有绑定同目录的原始采集数据')
        context_hash = digest({'values': {k: actual[k] for k in keys},
                               'versions': {k: request['parameterVersions'][k] for k in keys}})
        evidence = {'operationId': operation, 'rawSha256s': sorted(set(raw)),
                    'analysisArtifactSha256': analysis_item[0]['sha256'], 'calibrationContextSha256': context_hash,
                    'analysisArtifactId': analysis_item[0]['id'], 'planArtifactId': plan_item[0]['id']}
        return report, evidence

    def _apply_supplemental_acceptance(self, wf, request, result, stage, selection=None):
        snapshot = wf.get('supplementalAcceptance')
        if not snapshot or request['procedureId'] not in snapshot.get('applicableProcedures', []):
            return
        try:
            self._assert_supplemental_snapshot(wf)
            module = self._loaded_supplemental_module()
            bindings = {'selection': selection, 'verification': (request, result)} if stage == 'cross' else {stage: (request, result)}
            reports, evidence = {}, {}
            for role, pair in bindings.items():
                if not pair:
                    raise APIError('SUPPLEMENTAL_EVIDENCE_MISSING', '缺少独立采集配对')
                reports[role], evidence[role] = self._supplemental_evidence(pair[0], pair[1], snapshot)
            context = {'runId': request['runId'], 'resourceId': request['resourceId'],
                       **{k: snapshot[k] for k in ('baseGatesVersion', 'baseGatesSha256', 'sourceManifestSha256', 'moduleSha256')},
                       'policySha256': snapshot['policyHash']}
            receipt = module.evaluate_supplemental_quality(request['procedureId'], stage, reports,
                policy=snapshot['policySnapshot'], base_gates=wf['gates'], evidence=evidence, context=context)
            receipt['registeredEvidence'] = evidence
        except (APIError, OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            receipt = {'schema': 'qcal-supplemental-quality/1', 'ruleVersion': snapshot['policyVersion'],
                       'stage': stage, 'status': 'unverified', 'accepted': False, 'checks': [],
                       'reasons': [str(exc)], 'bindingError': getattr(exc, 'code', type(exc).__name__),
                       'policyHash': snapshot['policyHash'], 'evidenceHash': None,
                       'acquisitionClaim': 'derived_validation_only'}
        bundle = result.setdefault('supplementalQuality', {'policyVersion': snapshot['policyVersion'],
            'policyHash': snapshot['policyHash'], 'receipts': {}})
        bundle['receipts'][stage] = receipt
        bundle['accepted'] = all(bundle['receipts'].get(s, {}).get('accepted') is True for s in ('selection', 'verification', 'cross'))
        if receipt['accepted'] is not True:
            if result['quality']['status'] != 'failed':
                result['quality']['status'] = 'failed' if receipt['status'] == 'failed' else 'unverified'
            result['quality']['reasons'].extend('补充验收（' + stage + '）：' + reason for reason in receipt['reasons'] or ['证据不足'])

    def _legacy_decision_error(self, rt, workflow, node_id):
        """Recognize only the old post-acquisition BAD_EVIDENCE classification.

        No instrument/transport failure or partially acquired result is eligible.
        This does not rewrite the historical attempt; the operator may only ask
        the model for a new decision based on the retained completed evidence.
        """
        if (workflow.get('status') != 'attention' or workflow.get('engineState') != 'execution_failed'
                or not node_id or node_id != workflow.get('currentNodeId')):
            return False
        node = self._node(workflow, node_id)
        result = node.get('result') or {}
        messages = {'证据引用必须为编号列表', '决策引用了当前节点之外的证据'}
        if (node.get('detail') not in messages or result.get('dataKind') != 'real'
                or result.get('partial') or result.get('stale')
                or result.get('quality', {}).get('status') not in ('failed', 'unverified')):
            return False
        row = rt.db.execute('SELECT payload FROM platform_attempts WHERE id=? AND workflow_id=? AND node_id=?',
                            (node.get('latestAttemptId'), workflow['id'], node_id)).fetchone()
        if not row:
            return False
        attempt = json.loads(row['payload'])
        if attempt.get('state') != 'execution_failed' or attempt.get('error') != node['detail'] or attempt.get('result') != result:
            return False
        effects = list(rt.db.execute('SELECT state FROM platform_effects WHERE workflow_id=? AND attempt_id=?',
                                    (workflow['id'], attempt['id'])))
        return bool(effects) and all(row['state'] == 'completed' for row in effects)

    @staticmethod
    def _has_lease(rt, workflow_id):
        return rt.db.execute('SELECT 1 FROM platform_leases WHERE workflow_id=?', (workflow_id,)).fetchone() is not None

    def detail(self, workflow_id, node_id):
        wf = self.get(workflow_id)
        node = self._node(wf, node_id)
        attempts, decisions = self.store.detail(workflow_id, node_id)
        with self.store.connect() as rt:
            parameters, _ = self._parameters(rt, wf, node)
        return {'node': node, 'attempts': attempts, 'decisions': decisions, 'rules': node['rules'],
                'parameters': parameters, 'parameterBounds': self._parameter_schema(node['procedureId'])}

    def set_parameters(self, workflow_id, node_id, payload):
        with self.store.transaction() as rt:
            wf = self.get(workflow_id, rt)
            existing, request_hash = self._command(rt, wf, {**payload, 'nodeId': node_id}, 'parameters')
            if existing:
                return existing
            if wf['mode'] != 'real' or wf['status'] not in ('draft', 'paused', 'attention'):
                raise APIError('INVALID_STATE', '参数只能在真机任务草稿、暂停或待处理状态修改', 409)
            if self._has_lease(rt, workflow_id) or rt.db.execute("SELECT 1 FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')", (workflow_id,)).fetchone():
                raise APIError('RECONCILIATION_REQUIRED', '存在活动或未知采集，先完成回执核查', 409)
            self._assert_adapter_binding(wf)
            node = self._node(wf, node_id)
            changes = payload.get('parameters')
            if not isinstance(changes, dict) or not changes:
                raise APIError('BAD_PARAMETERS', '请提供需要修改的参数')
            schema = self._parameter_schema(node['procedureId'])
            for key, value in changes.items():
                self._validate_parameter(key, value, schema)
            parameters, versions = self._parameters(rt, wf, node)
            requested_changes = deepcopy(changes)
            changes = self._normalize_changes(node['procedureId'], changes, parameters)
            for key, value in changes.items():
                self._validate_parameter(key, value, schema)
            merged = {**parameters, **changes}
            marker = self._readout_revalidation(rt, wf, node)
            merged = self._readout_revalidation_parameters(node, merged, marker)
            self._validate_plan(wf, node, merged, versions, payload['operationId'])
            before = {key: parameters.get(key) for key in changes}
            node.setdefault('parameterOverrides', {}).update(changes)
            node['parameters'] = merged
            affected = self._invalidate(wf, node_id, include_target=True)
            if wf['status'] != 'draft':
                wf['status'], wf['engineState'] = 'paused', 'parameters_updated'
            self._record_decision(rt, wf, node_id, {'action': 'parameters_updated', 'reason': '操作员保存参数；未启动采集',
                'evidenceIds': [], 'parameterChanges': {key: {'from': before[key], 'to': value} for key, value in changes.items()},
                **({'requestedParameterChanges': requested_changes, 'normalization': '执行器脉冲策略及采样网格规范化'} if changes != requested_changes else {}),
                'invalidatedNodes': affected}, 'operator')
            self.store.save(rt, wf, 'parameters_updated', {'nodeId': node_id, 'message': '参数已保存，相关结果已标记失效；等待明确启动或继续'})
            rt.db.execute('INSERT INTO platform_commands VALUES(?,?,?)', (payload['operationId'], request_hash, dumps(wf)))
        return wf

    def _validate_plan(self, wf, node, parameters, versions, operation_id, *, joint_budget=False, rt=None):
        if not callable(getattr(self.adapter, 'build_plan', None)):
            if joint_budget and self._physical_adapter:
                raise APIError('INVALID_BUDGET', '物理执行器缺少可核查的采集计划')
            return
        request = {'runId': wf['id'], 'nodeId': node['id'], 'attemptId': 'parameter-validation',
                   'operationId': 'validate/' + operation_id, 'resourceId': node['resourceId'],
                   'procedureId': node['procedureId'], 'parameters': parameters, 'parameterVersions': versions,
                   'deadline': time.time() + self.profile.get('maximumRunSeconds', 3600)}
        if node['procedureId'] in ('sq_rb', 'sq_allxy'):
            with (nullcontext(rt) if rt is not None else self.store.connect()) as reader:
                request['validationSnapshot'] = self._validation_snapshot(reader, wf, node, parameters, versions)
        try:
            plan = self.adapter.build_plan(request)
            if joint_budget and self._physical_adapter:
                return self._selection_budget(wf, request, plan)
        except Exception as exc:
            raise APIError(getattr(exc, 'code', 'INVALID_PLAN'), str(exc)) from None

    def _selection_budget(self, wf, request, plan):
        """Plan-only lookahead; actual per-operation reservation remains in _call_adapter."""
        adapter_type = type(self.adapter)
        parameters, basis = verification_probe(
            implementation_id=adapter_type.__module__ + '.' + adapter_type.__qualname__,
            profile_id=self.profile.get('id'), procedure_id=request['procedureId'],
            request_parameters=request['parameters'], selection_plan=plan)
        probe = deepcopy(request)
        probe['parameters'] = parameters
        probe['operationId'] = 'budget-verify/' + digest(request['operationId'])
        verification_plan = self.adapter.build_plan(probe)
        return selection_lookahead(wf, plan, verification_plan, basis=basis)

    def artifact(self, artifact_id):
        with self.store.connect() as rt:
            row = rt.db.execute('SELECT * FROM platform_artifacts WHERE id=?', (artifact_id,)).fetchone()
        if not row:
            raise APIError('NOT_FOUND', '产物未登记', 404)
        path = checked_path(row['path'])
        if not path.is_file() or file_hash(path) != row['sha256']:
            raise APIError('ARTIFACT_HASH_MISMATCH', '产物已丢失或内容变化', 409)
        return path, json.loads(row['payload'])

    def _loop(self):
        while not self.stop_event.is_set():
            queued = [w for w in reversed(self.list()) if w['status'] == 'queued']
            if queued:
                try:
                    self.run_workflow(queued[0]['id'])
                except Exception as exc:
                    self._attention(queued[0]['id'], 'controller_error', str(exc))
            else:
                self.wake.wait(.5)
                self.wake.clear()

    def _attention(self, workflow_id, state, message, *, node_id=None, preserve_diagnostic_stop=False):
        with self.store.transaction() as rt:
            wf = self.get(workflow_id, rt)
            if (preserve_diagnostic_stop and wf.get('rbDiagnostic')
                    and (wf['status'] in ('paused', 'aborted') or wf.get('cancelRequested')
                         or wf.get('engineState') == 'needs_reconciliation')):
                return
            if wf['status'] in TERMINAL:
                return
            if node_id is not None:
                self._node(wf, node_id)
                wf['currentNodeId'] = node_id
            wf['status'], wf['engineState'] = 'attention', state
            if wf.get('currentNodeId'):
                node = self._node(wf)
                node['status'], node['engineState'], node['detail'] = 'attention', state, message
            self.store.save(rt, wf, 'attention', {'message': message})

    def run_workflow(self, workflow_id):
        wf = self.get(workflow_id)
        if wf['status'] != 'queued':
            return
        if wf['mode'] == 'replay':
            return self._replay(workflow_id)
        try:
            self._assert_adapter_binding(wf)
        except APIError as exc:
            return self._attention(workflow_id, 'adapter_changed', exc.message, preserve_diagnostic_stop=True)
        if wf['engineState'] == 'decision_queued':
            if not self._decide(workflow_id, wf['currentNodeId']):
                return
            wf = self.get(workflow_id)
        preflight = self.adapter.preflight()
        if not preflight.get('ready'):
            return self._attention(workflow_id, 'preflight_failed', '当前执行器静态预检未通过，未连接设备', preserve_diagnostic_stop=True)
        if not preflight.get('enabled', self.profile.get('enabled', False)) and not wf['hardwareConfirmed']:
            return self._attention(workflow_id, 'waiting_hardware', '当前执行器尚未启用；等待现场接线和设备独占交接', preserve_diagnostic_stop=True)
        if not (wf['hardwareConfirmed'] or self.profile.get('connectionConfirmedAt')):
            return self._attention(workflow_id, 'waiting_hardware', '请确认当前是主机读出/XY/采集卡接线', preserve_diagnostic_stop=True)
        ownership = self.hardware_ownership(refresh=True)
        if not ownership['verified'] or ownership['owners']:
            return self._attention(workflow_id, 'waiting_hardware', '检测到其他程序仍连接机箱，等待释放；未下发测量', preserve_diagnostic_stop=True)
        diagnostic = wf.get('rbDiagnostic') or {}
        if diagnostic.get('state') == 'queued':
            return self._run_rb_diagnostic(workflow_id, diagnostic['id'])
        if diagnostic and wf.get('currentNodeId') == diagnostic.get('targetNodeId'):
            return self._attention(workflow_id, 'diagnostic_closed', '单次RB诊断许可已关闭，未自动重发或推进原流程', preserve_diagnostic_stop=True)
        while not self.stop_event.is_set():
            wf = self.get(workflow_id)
            if wf['status'] not in ('queued', 'running'):
                return
            if wf.get('cancelRequested'):
                return self._finish_cancel(workflow_id)
            pending = [n for n in wf['nodes'] if n['engineState'] not in ('verified', 'acknowledged', 'unsupported')]
            ready = [n for n in pending if all(self._node(wf, d)['engineState'] == 'verified' for d in n['dependsOn'])]
            if not pending:
                return self._complete(workflow_id)
            if not ready:
                return self._attention(workflow_id, 'prerequisites_missing', '前置校准未验证或已失效，等待复测')
            node = ready[0]
            if node['capability'] != 'available':
                return self._attention(workflow_id, 'unsupported_method', '当前方法尚未接入：' + node['label'])
            if wf['iterations'] >= wf['maxWorkflowAttempts'] or node['attempt'] >= wf['maxAttemptsPerNode']:
                return self._attention(workflow_id, 'budget_exhausted', '达到本轮测量预算，保留全部候选与结果', node_id=node['id'])
            if not self._execute_node(workflow_id, node['id']):
                return

    def _replay(self, workflow_id):
        with self.store.transaction() as rt:
            wf = self.get(workflow_id, rt)
            wf['status'], wf['engineState'] = 'running', 'replaying'
            self.store.save(rt, wf, 'replay_started', {'message': '开始回放已登记的真实历史记录；不会连接仪器'})
        # Replay follows original chronology, preserving every procedure and attempt.
        records = sorted((r for r in self.records if r['resourceId'] in wf['resourceIds']),
                         key=lambda r: (r['result'].get('acquiredAt') or '', r['id']))
        for index, record in enumerate(records):
            if self.stop_event.is_set():
                return
            with self.store.transaction() as rt:
                wf = self.get(workflow_id, rt)
                if wf.get('cancelRequested'):
                    self._finish_cancel_locked(rt, wf)
                    return
                if record['id'] in wf['processedRecordIds']:
                    continue
                node_id = record['resourceId'] + ':' + record['ownerId']
                node = self._node(wf, node_id)
                node['attempt'] += 1
                result = evaluate(record['result'], record['procedureId'], 'replay')
                result['artifacts'] = [self._artifact(rt, a, verify=True) for a in result['artifacts']]
                attempt = {'id': ident('attempt'), 'nodeId': node_id, 'number': node['attempt'], 'state': 'historical',
                           'startedAt': now(), 'endedAt': now(), 'parameters': record['parameters'], 'parameterVersions': {},
                           'result': result, 'operationId': wf['id'] + '/replay/' + record['id'], 'mode': 'replay',
                           'procedureId': record['procedureId'], 'sourceRecordId': record['id']}
                self.store.attempt(rt, wf['id'], attempt)
                node.update(result=result, parameters=deepcopy(record['parameters']), latestAttemptId=attempt['id'],
                            status='attention', engineState='historical', progress=100,
                            detail='历史实测：' + result.get('methodLabel', record['procedureId']) + '；当前校准未验证')
                wf['currentNodeId'], wf['progress'] = node_id, round((index + 1) * 100 / max(1, len(records)))
                wf['iterations'] += 1
                wf['processedRecordIds'].append(record['id'])
                self.store.save(rt, wf, 'historical_record', {'message': f"回放 {record['resourceId']} · {result.get('methodLabel', record['procedureId'])}", 'recordId': record['id'], 'nodeId': node_id})
            self.stop_event.wait(.08)
        self._complete(workflow_id)

    def _complete(self, workflow_id):
        with self.store.transaction() as rt:
            wf = self.get(workflow_id, rt)
            passed = [n for n in wf['nodes'] if n['engineState'] == 'verified']
            wf['workflowComplete'] = wf['mode'] == 'real' and len(passed) == len(wf['nodes'])
            if wf['mode'] == 'real' and not wf['workflowComplete']:
                wf['status'], wf['engineState'] = 'attention', 'coverage_incomplete'
                wf['progress'] = round(100 * len(passed) / max(1, len(wf['nodes'])))
            else:
                wf['status'], wf['engineState'], wf['progress'] = 'completed', ('verified' if wf['workflowComplete'] else 'replay_completed'), 100
            self._finish_timing(wf)
            wf['resultSummary'] = {'verifiedNodes': len(passed), 'totalNodes': len(wf['nodes']),
                                   'coverage': wf.get('coverage', 'main'),
                                   'recordCount': len(wf['processedRecordIds']), 'workflowComplete': wf['workflowComplete'],
                                   'meaning': '历史记录读取完成，不等同于本轮全部实验通过' if wf['mode'] == 'replay' else ('本轮所选测项全部独立验证完成' if wf['workflowComplete'] else '本轮测项尚未全部完成，详见能力和质量状态')}
            self.store.save(rt, wf, 'completed' if wf['status'] == 'completed' else 'coverage_incomplete', {'message': wf['resultSummary']['meaning']})

    def _finish_cancel_locked(self, rt, wf):
        action = wf.get('cancelRequested') or 'pause'
        wf['status'] = 'aborted' if action == 'abort' else 'paused'
        wf['engineState'], wf['cancelRequested'] = wf['status'], None
        diagnostic = wf.get('rbDiagnostic') or {}
        if diagnostic.get('state') in ('queued', 'running'):
            diagnostic.update(state='interrupted', finishedAt=now(), resultStatus='unverified')
            wf['workflowComplete'] = False
        self._finish_timing(wf)
        self.store.save(rt, wf, wf['status'], {'message': '已在无活动采集的检查点' + ('终止' if action == 'abort' else '暂停')})

    def _finish_cancel(self, workflow_id):
        with self.store.transaction() as rt:
            wf = self.get(workflow_id, rt)
            if self._has_lease(rt, workflow_id):
                wf['status'], wf['engineState'] = 'attention', 'needs_reconciliation'
                self.store.save(rt, wf, 'reconciliation_required', {'message': '停止状态尚未确认，保持设备隔离'})
            else:
                self._finish_cancel_locked(rt, wf)

    def _parameters(self, rt, wf, node):
        prefix = wf['id'] + ':' + node['resourceId'] + ':'
        values = {k: v for k, v in self.profile.get('resources', {}).get(node['resourceId'], {}).items()
                  if k not in ('q', 'calibrationStatus') and (number(v) or k in self.profile.get('parameterBounds', {}))}
        versions = {k: 0 for k in values}
        for key, entry in rt.get_parameters().items():
            if key.startswith(prefix):
                values[key[len(prefix):]], versions[key[len(prefix):]] = entry['value'], entry['revision']
        # Observed parameters are snapshots, not overrides. A retry/backtrack
        # must inherit freshly committed upstream values unless explicitly tuned.
        values.update(node.get('parameterOverrides', {}))
        return values, versions

    def _readout_revalidation(self, rt, wf, node, parameters=None, versions=None):
        marker = wf.get('readoutRevalidations', {}).get(node['resourceId'])
        if not marker or marker['state'] not in ('active', 'fixed_failed'):
            return None
        # An uncommitted override is not a new accepted working point and must
        # never release the fixed-point requirement.
        parameters, versions = self._parameters(rt, wf, {**node, 'parameterOverrides': {}})
        mismatches = [k for k, value in marker['values'].items()
                      if not self._same_parameter(value, parameters.get(k)) or marker['parameterVersions'][k] != versions.get(k, 0)]
        if mismatches:
            marker.update(state='invalidated', endedAt=now(), reason='读出工作点或版本已改变：' + ', '.join(mismatches))
            self._record_decision(rt, wf, node['id'], {'action': 'invalidate', 'reason': marker['reason'],
                'evidenceIds': [], 'revalidationId': marker['id'], 'ruleId': 'readout-revalidation/1'}, 'rule')
            return None
        return marker

    def _readout_revalidation_parameters(self, node, parameters, marker):
        if not marker:
            return parameters
        if node['id'] in marker.get('lockedNodeIds', [marker['optimizerNodeId']]):
            mismatches = [k for k, v in marker['values'].items() if not self._same_parameter(parameters.get(k), v)]
            if mismatches:
                raise APIError('READOUT_FIXED_WORKING_POINT', '当前读出重建不允许覆盖已提交工作点：' + ', '.join(mismatches))
        if node['id'] != marker['optimizerNodeId']:
            return parameters
        hook = getattr(self.adapter, 'build_verification_parameters', None)
        if not callable(hook):
            raise APIError('READOUT_REVALIDATION_UNSUPPORTED', '执行器尚未支持固定读出候选验证')
        return hook(node['procedureId'], deepcopy(parameters), deepcopy(marker['values']))

    def _readout_retry_parameters(self, rt, wf, node, parameters, versions, changes):
        marker = self._readout_revalidation(rt, wf, node, parameters, versions)
        trial = {**parameters, **changes}
        if not marker or node['id'] not in marker.get('lockedNodeIds', [marker['optimizerNodeId']]):
            return trial, None
        if any(k in changes and not self._same_parameter(changes[k], v) for k, v in marker['values'].items()):
            raise APIError('READOUT_FIXED_WORKING_POINT', '当前重建固定已提交读出工作点；需通过固定点验证失败后的范围重扫选择新候选')
        scan_keys = {'amp_start', 'amp_stop'}
        if node['id'] == marker['optimizerNodeId'] and scan_keys.intersection(changes):
            if not scan_keys.issubset(changes):
                raise APIError('READOUT_RESCAN_RANGE_REQUIRED', '读出重扫必须同时明确 amp_start 和 amp_stop')
            if (marker['state'] != 'fixed_failed' or marker.get('lastFixedAttemptId') != node.get('latestAttemptId')
                    or node.get('result', {}).get('quality', {}).get('status') != 'failed' or node.get('result', {}).get('stale')):
                raise APIError('READOUT_FIXED_VALIDATION_REQUIRED', '必须先完成当前候选的固定点验证并确认未通过，才能选择重新全扫')
            if changes['amp_stop'] <= changes['amp_start'] or not callable(getattr(self.adapter, 'build_plan', None)):
                raise APIError('READOUT_RESCAN_INVALID', '读出重扫需要递增范围及执行器网格校验')
            trial.pop('amplitudes', None)
            return trial, marker
        return self._readout_revalidation_parameters(node, trial, marker), None

    @staticmethod
    def _readout_revalidation_quality(request, result):
        marker = request.get('readoutRevalidation')
        if not marker or request['nodeId'] != marker['optimizerNodeId']:
            return
        reasons = []
        actual = result.get('parameters') or {}
        for key, value in marker['values'].items():
            if not Controller._same_parameter(actual.get(key), value):
                reasons.append('固定读出重建回执工作点不符：' + key)
        amplitudes = actual.get('amplitudes')
        if not isinstance(amplitudes, list) or len(amplitudes) != 1 or not Controller._same_parameter(amplitudes[0], marker['values']['readout_amp']):
            reasons.append('读出重建必须只验证已选幅度，不能重新全扫选优')
        candidate = result.get('candidateParameters')
        if not isinstance(candidate, dict) or not Controller._same_parameter(candidate.get('readout_amp'), marker['values']['readout_amp']):
            reasons.append('固定读出候选未被本轮采集确认')
        if isinstance(candidate, dict) and 'readout_hz' in candidate and not Controller._same_parameter(candidate['readout_hz'], marker['values']['readout_hz']):
            reasons.append('固定读出频率候选不符')
        if reasons:
            result['quality']['status'] = 'failed'
            result['quality']['reasons'].extend(reasons)

    def _start_readout_revalidation(self, rt, wf, optimizer):
        nodes = {n['procedureId']: n for n in wf['nodes'] if n['resourceId'] == optimizer['resourceId']}
        rabi, time_rabi, iq = (nodes.get(k) for k in ('sq_rabi_amplitude', 'sq_rabi_time', 'ro_iq01'))
        if (not all((rabi, time_rabi, iq)) or rabi['id'] not in time_rabi['dependsOn']
                or time_rabi['id'] not in iq['dependsOn'] or iq['id'] not in optimizer['dependsOn']):
            raise APIError('READOUT_REVALIDATION_UNSUPPORTED', '当前蓝图缺少完整 Rabi→时间 Rabi→IQ→读出优化依赖链，不能宣称完成新读出工作点重建')
        values, versions = self._parameters(rt, wf, {**optimizer, 'parameterOverrides': {}})
        required = ('readout_hz', 'readout_amp')
        if any(not number(values.get(k)) for k in required):
            raise APIError('READOUT_REVALIDATION_UNSUPPORTED', '读出重建缺少完整的频率和幅度工作点')
        markers = wf.setdefault('readoutRevalidations', {})
        if optimizer['resourceId'] in markers:
            wf.setdefault('readoutRevalidationHistory', []).append(deepcopy(markers[optimizer['resourceId']]))
        marker = {'id': ident('readout-revalidation'), 'ruleVersion': 'readout-revalidation/1',
                  'resourceId': optimizer['resourceId'], 'optimizerNodeId': optimizer['id'],
                  'rabiNodeId': rabi['id'], 'sourceAttemptId': optimizer.get('latestAttemptId'),
                  'values': {k: values[k] for k in required},
                  'parameterVersions': {k: versions.get(k, 0) for k in required},
                  'state': 'active', 'createdAt': now()}
        markers[optimizer['resourceId']] = marker
        affected = self._invalidate(wf, rabi['id'], include_target=True)
        marker['lockedNodeIds'] = list(affected)
        removed_overrides = {}
        for node in wf['nodes']:
            if node['id'] in affected:
                old = node.get('parameterOverrides', {})
                removed = {k: old.pop(k) for k in required if k in old}
                if removed:
                    removed_overrides[node['id']] = removed
        wf.update(currentNodeId=rabi['id'], status='paused' if wf['policy'] == 'strict' else 'queued',
                  engineState='readout_revalidation_pending' if wf['policy'] == 'strict' else 'backtrack_queued')
        wf['progress'] = round(100 * sum(n['engineState'] == 'verified' for n in wf['nodes']) / len(wf['nodes']))
        rabi['detail'] = '读出工作点已更新；从 Rabi 开始重建当前校准，随后固定候选复验读出'
        self._record_decision(rt, wf, optimizer['id'], {'action': 'backtrack', 'reason': rabi['detail'],
            'targetNodeId': rabi['id'], 'invalidatedNodes': affected, 'evidenceIds': [],
            'revalidationId': marker['id'], 'ruleId': marker['ruleVersion'],
            'removedReadoutOverrides': removed_overrides}, 'rule')
        return rabi['id']

    def _execute_node(self, workflow_id, node_id, *, diagnostic_branch_id=None):
        wf_before = self.get(workflow_id)
        self._assert_adapter_binding(wf_before)
        try:
            supplemental = self._assert_supplemental_snapshot(wf_before)
        except (APIError, OSError, ValueError, TypeError) as exc:
            self._attention(workflow_id, 'supplemental_acceptance_mismatch', str(exc), node_id=node_id)
            return False
        node_before = self._node(wf_before, node_id)
        if node_before['procedureId'] == 'sq_rb' and diagnostic_branch_id is None:
            if not self._rb_prerequisites_current(workflow_id, node_id):
                return False
        with self.store.transaction() as rt:
            wf, attempt_id = self.get(workflow_id, rt), ident('attempt')
            node = self._node(wf, node_id)
            if diagnostic_branch_id is not None:
                self._assert_rb_diagnostic_dispatch(rt, wf, node, diagnostic_branch_id)
            group = self.profile.get('instrument', {}).get('resourceGroup', 'CY8_SHARED_BUS1')
            if rt.db.execute('SELECT 1 FROM platform_leases WHERE resource=?', (group,)).fetchone():
                wf['status'], wf['engineState'] = 'attention', 'resource_busy'
                self.store.save(rt, wf, 'attention', {'message': '共享仪器仍占用或隔离中'})
                return False
            parameters, versions = self._parameters(rt, wf, node)
            marker = self._readout_revalidation(rt, wf, node, parameters, versions)
            parameters = self._readout_revalidation_parameters(node, parameters, marker)
            lookahead = None
            if self._physical_adapter:
                try:
                    lookahead = self._validate_plan(wf, node, parameters, versions, attempt_id,
                                                    joint_budget=True, rt=rt)
                except APIError as exc:
                    # Admission is before lease, effect, attempt count or result replacement.
                    wf.update(status='attention', engineState='budget_exhausted' if 'BUDGET' in exc.code else 'plan_rejected')
                    node.update(status='attention', engineState=wf['engineState'], detail=exc.message,
                                lastAdmissionError={'code': exc.code, 'message': exc.message, 'at': now()})
                    self.store.save(rt, wf, 'acquisition_admission_rejected',
                                    {'nodeId': node_id, 'errorCode': exc.code, 'message': exc.message})
                    return False
            token = ident('lease')
            rt.db.execute('INSERT INTO platform_leases VALUES(?,?,?,?,?)', (group, workflow_id, attempt_id, 'active', token))
            node['attempt'] += 1
            attempt = {'id': attempt_id, 'nodeId': node_id, 'number': node['attempt'], 'state': 'running', 'startedAt': now(),
                       'parameters': parameters, 'parameterVersions': versions, 'operationId': attempt_id + '/measure',
                       'mode': 'real', 'procedureId': node['procedureId']}
            request = {'runId': workflow_id, 'nodeId': node_id, 'attemptId': attempt_id,
                       'operationId': attempt['operationId'], 'resourceId': node['resourceId'], 'procedureId': node['procedureId'],
                       'parameters': parameters, 'parameterVersions': versions,
                       'adapterId': self.adapter_id, 'adapterProfileHash': self.adapter_profile_hash,
                       'deadline': time.time() + self.profile.get('maximumRunSeconds', 3600),
                       'outputDir': str(self.root / 'data' / 'runs' / workflow_id / attempt_id), 'leaseToken': token}
            if diagnostic_branch_id is not None:
                wf['rbDiagnostic'].update(state='running', attemptId=attempt_id)
                branch = {k: wf['rbDiagnostic'][k] for k in ('id','policyVersion','commandOperationId',
                    'sourceNodeId','sourceAttemptId','sourceProofSha256','publication')}
                request['rbDiagnostic'] = deepcopy(branch)
                attempt['rbDiagnostic'] = deepcopy(branch)
            if lookahead is not None:
                request['budgetLookahead'] = deepcopy(lookahead)
                attempt['budgetLookahead'] = deepcopy(lookahead)
            if marker:
                request['readoutRevalidation'] = deepcopy(marker)
                attempt['readoutRevalidation'] = deepcopy(marker)
                if node_id == marker['optimizerNodeId']:
                    marker['state'] = 'active'
            request['validationSnapshot'] = self._validation_snapshot(rt, wf, node, parameters, versions)
            if supplemental and node['id'] in supplemental['applicableNodeIds']:
                request['supplementalAcceptance'] = {k: supplemental[k] for k in
                    ('policyVersion', 'policyHash', 'moduleSha256', 'sourceManifestSha256', 'baseGatesSha256')}
            rt.db.execute('INSERT INTO platform_effects VALUES(?,?,?,?,?,NULL)',
                          (request['operationId'], workflow_id, attempt_id, 'pending', dumps(request)))
            self.store.attempt(rt, workflow_id, attempt)
            node['lastCompletedAttemptId'] = node.get('latestAttemptId')
            node.pop('result', None)
            node.pop('telemetry', None)
            node.update(status='running', engineState='acquiring', latestAttemptId=attempt_id,
                        parameters=parameters, parameterVersions=versions, progress=0,
                        detail='正在采集本轮新数据')
            wf.update(status='running', engineState='acquiring', currentNodeId=node_id, iterations=wf['iterations'] + 1)
            if not wf.get('firstAcquisitionAt'):
                wf['firstAcquisitionAt'] = attempt['startedAt']
            self.store.save(rt, wf, 'acquisition_started', {'message': node['qubits'] + ' · ' + node['label'], 'attemptId': attempt_id})
        preliminary = verification = verify_request = None
        try:
            result = self._call_adapter(request, token)
            if result is None:
                return False
            preliminary = evaluate(result, request['procedureId'], 'real', wf['gates'], independent=False)
            self._readout_revalidation_quality(request, preliminary)
            self._apply_supplemental_acceptance(wf, request, preliminary, 'selection')
            if diagnostic_branch_id is not None:
                self._rb_diagnostic_candidate_contract(preliminary, 'selection')
            candidate = self._validated_candidate(request['procedureId'], preliminary, 'selection')
            # Only start independent verification once all numerical gates are already satisfied.
            gate_reasons = [r for r in preliminary['quality']['reasons'] if r != '需要独立采集复测']
            independent = False
            verification = None
            if not gate_reasons and not self._cancel_requested(workflow_id):
                verify_request = deepcopy(request)
                verify_request['operationId'] = attempt_id + '/verify'
                # Each independent acquisition has its own operation time limit.
                verify_request['deadline'] = time.time() + self.profile.get('maximumRunSeconds', 3600)
                raw_candidate = deepcopy(result.get('candidateParameters', {}))
                for key, value in candidate.items():
                    verify_request['parameters'][key] = value
                verification_parameters = getattr(self.adapter, 'build_verification_parameters', None)
                if callable(verification_parameters):
                    verify_request['parameters'] = verification_parameters(request['procedureId'], verify_request['parameters'], candidate)
                with self.store.transaction() as rt:
                    current = self.get(workflow_id, rt)
                    if diagnostic_branch_id is not None:
                        self._assert_rb_diagnostic_dispatch(rt, current, self._node(current, node_id), diagnostic_branch_id, attempt_id=attempt_id)
                    # The first acquisition is complete and registered. Publish it
                    # now, without implying that independent verification passed.
                    attempt['result'] = {'dataKind': 'real', 'partial': True, 'verificationPending': True,
                        'metrics': {}, 'artifacts': [],
                        'quality': {'status': 'unverified', 'reasons': ['首测已完成；独立复测尚未验收']},
                        'selectionResult': deepcopy(preliminary)}
                    self._node(current).update(engineState='verifying', detail='正在独立复测候选参数',
                        result=deepcopy(attempt['result']), mainProcedureComplete=False)
                    verify_request['validationSnapshot'] = self._validation_snapshot(rt, current, self._node(current), verify_request['parameters'], versions)
                    if self._physical_adapter:
                        # Candidate changes are now known. Check only verification's
                        # actual cost; selection has already consumed its reservation.
                        verify_request['verificationBudgetCheck'] = verify_admission(
                            current, self.adapter.build_plan(verify_request), request.get('budgetLookahead'))
                    rt.db.execute('INSERT INTO platform_effects VALUES(?,?,?,?,?,NULL)',
                                  (verify_request['operationId'], workflow_id, attempt_id, 'pending', dumps(verify_request)))
                    self.store.attempt(rt, workflow_id, attempt)
                    self.store.save(rt, current, 'verification_started', {'message': '开始新的独立采集验证候选参数'})
                verification = self._call_adapter(verify_request, token)
                if verification is None:
                    return False
                source_ids = {a['sha256'] for a in result.get('artifacts', []) if a['kind'] == 'raw'}
                verify_ids = {a['sha256'] for a in verification.get('artifacts', []) if a['kind'] == 'raw'}
                independent = bool(source_ids and verify_ids and source_ids.isdisjoint(verify_ids))
                result = evaluate(verification, request['procedureId'], 'real', wf['gates'], independent=independent)
                self._readout_revalidation_quality(verify_request, result)
                if preliminary.get('supplementalQuality'):
                    result['supplementalQuality'] = deepcopy(preliminary['supplementalQuality'])
                self._apply_supplemental_acceptance(wf, verify_request, result, 'verification')
                self._apply_supplemental_acceptance(wf, verify_request, result, 'cross', selection=(request, preliminary))
                if diagnostic_branch_id is not None:
                    self._rb_diagnostic_candidate_contract(result, 'verification')
                repeated_candidate = self._validated_candidate(request['procedureId'], result, 'verification')
                consistency = self._repeatability(candidate, repeated_candidate,
                                                  preliminary.get('parameters') or request['parameters']) if repeated_candidate is not None else []
                if consistency:
                    result['quality']['status'] = 'failed'
                    result['quality']['reasons'].extend(consistency)
                result['selectionResult'] = preliminary
                result['verificationCandidateParameters'] = deepcopy(verification.get('candidateParameters', {}))
                result['candidateParameters'] = candidate
                if candidate != raw_candidate:
                    result['candidateNormalization'] = {'requested': raw_candidate, 'applied': candidate,
                                                        'reason': '执行器脉冲策略及采样网格规范化', 'adapterId': self.adapter_id}
            else:
                result = preliminary
            with self.store.transaction() as rt:
                current = self.get(workflow_id, rt)
                current_node = self._node(current, node_id)
                if diagnostic_branch_id is not None:
                    self._assert_rb_diagnostic_dispatch(rt, current, current_node, diagnostic_branch_id, attempt_id=attempt_id, allow_cancel=True)
                    result['diagnosticScope'] = {**deepcopy(request['rbDiagnostic']),
                        'scope': 'rb_only_after_failed_cpmg', 'workflowComplete': False,
                        'sourceQualityStatus': 'failed', 'publication': 'disabled_diagnostic_only'}
                rebuild_target = None
                if result['quality']['status'] == 'passed':
                    context_reasons = self._calibration_context_reasons(rt, current, current_node, result)
                    if context_reasons:
                        result['quality']['status'] = 'failed'
                        result['quality']['reasons'].extend(context_reasons)
                if result.get('supplementalQuality') and result['quality']['status'] != 'passed':
                    # Already registered by _call_adapter. Preserve the original
                    # SHA metadata plus rejection receipt when a later integrity
                    # check fails; never replace the native file or its hash.
                    result['artifacts'] = deepcopy(result.get('artifacts', []))
                else:
                    result['artifacts'] = [self._artifact(rt, a) for a in result.get('artifacts', [])]
                attempt.update(state='verified' if result['quality']['status'] == 'passed' else 'attention', endedAt=now(), result=result,
                               verificationOperationId=attempt_id + '/verify' if verification else None)
                self.store.attempt(rt, workflow_id, attempt)
                passed = result['quality']['status'] == 'passed'
                current_node.update(result=result, progress=100, status='success' if passed else 'attention',
                                    engineState='verified' if passed else 'quality_attention', mainProcedureComplete=passed,
                                    detail='独立复测通过' if passed else '；'.join(result['quality']['reasons']))
                if diagnostic_branch_id is not None:
                    current_node['diagnosticScope'] = deepcopy(result['diagnosticScope'])
                    current_node['detail'] = ('RB诊断独立验收通过；CPMG仍未通过，不发布完整校准' if passed else
                                              'RB诊断未通过：' + '；'.join(result['quality']['reasons']))
                # Finish receipts are not claims that the physical RF output is proven OFF.
                rt.db.execute('DELETE FROM platform_leases WHERE workflow_id=? AND token=?', (workflow_id, token))
                if passed:
                    if diagnostic_branch_id is None:
                        rebuild_target = self._commit_candidate(rt, current, current_node, result.get('candidateParameters', {}), versions)
                    if diagnostic_branch_id is None and current_node['procedureId'] == 'sq_rb' and self._physical_adapter and callable(getattr(self.adapter, 'publish_calibration', None)):
                        snapshot = self._validation_snapshot(rt, current, current_node, parameters, versions)
                        snapshot['acceptedEvidence'].append({'procedureId': 'sq_rb', 'status': 'passed',
                            'selectionOperationId': attempt['operationId'], 'verificationOperationId': attempt['verificationOperationId'],
                            'ruleVersion': current['gatesVersion']})
                        calibration = self.adapter.publish_calibration(snapshot)
                        result['artifacts'].append(self._artifact(rt, calibration))
                        current_node['calibrationVersion'] = calibration['calibrationVersion']
                        attempt['result'] = result
                        self.store.attempt(rt, workflow_id, attempt)
                    previous = [json.loads(r[0]) for r in rt.db.execute('SELECT payload FROM platform_decisions WHERE workflow_id=? AND node_id=? ORDER BY rowid DESC',
                                                                       (workflow_id, node_id))]
                    prior_decision = next((d for d in previous if d.get('source') == 'model' and d.get('action') == 'retry'), None)
                    if prior_decision:
                        candidate_experience = self.experience.observe_verified(rt, current, current_node, attempt, prior_decision)
                        if candidate_experience:
                            current_node['experienceCandidateId'] = candidate_experience['id']
                current_marker = self._readout_revalidation(rt, current, current_node)
                if request.get('readoutRevalidation') and current_marker and node_id == current_marker['optimizerNodeId']:
                    current_marker.update(state='completed' if passed else 'fixed_failed',
                                          lastFixedAttemptId=attempt_id)
                    if current_marker.get('origin') == 'operator_withdrawal':
                        current_marker['validationStatus'] = 'verified' if passed else 'failed'
                    if passed:
                        current_marker['endedAt'] = now()
                current['progress'] = round(100 * sum(n['engineState'] == 'verified' for n in current['nodes']) / len(current['nodes']))
                self.store.save(rt, current, 'readout_revalidation_started' if rebuild_target else 'node_verified' if passed else 'quality_attention',
                                {'message': self._node(current)['detail'] if rebuild_target else current_node['label'] + '：' + current_node['detail'], 'attemptId': attempt_id})
            if self._cancel_requested(workflow_id):
                self._finish_cancel(workflow_id)
                return False
            if diagnostic_branch_id is not None:
                # One explicit permission ends here, on either scientific outcome.
                # It never enters normal approve/autopilot/publication scheduling.
                return False
            if passed:
                if rebuild_target:
                    return current['status'] == 'queued'
                if current['policy'] == 'strict':
                    self._attention(workflow_id, 'awaiting_acceptance', '独立复测通过，等待确认后推进')
                    return False
                return True
            return self._decide(workflow_id, node_id)
        except Exception as exc:
            with self.store.transaction() as rt:
                current = self.get(workflow_id, rt)
                if diagnostic_branch_id is not None and isinstance(exc, APIError) and exc.code.startswith('RB_DIAGNOSTIC_'):
                    diagnostic = current.get('rbDiagnostic') or {}
                    if diagnostic.get('id') == diagnostic_branch_id:
                        diagnostic.update(state='invalidated', errorCode=exc.code)
                stored_node = self._node(current, node_id)
                stored_result = stored_node.get('result') or {}
                if stored_node.get('latestAttemptId') == attempt_id and stored_result.get('verificationPending'):
                    # A failed final commit may roll back the database while the
                    # local attempt already holds a passed result. Use the durable
                    # unaccepted placeholder before clearing pending below.
                    attempt['result'] = deepcopy(stored_result)
                pending = rt.db.execute("SELECT 1 FROM platform_effects WHERE attempt_id=? AND state IN ('pending','unknown')", (attempt_id,)).fetchone()
                state = 'needs_reconciliation' if pending else 'execution_failed'
                if not pending and isinstance(exc, APIError) and 'BUDGET' in exc.code:
                    state = 'budget_exhausted'
                if pending:
                    rt.db.execute("UPDATE platform_leases SET state='quarantined' WHERE token=?", (token,))
                else:
                    rt.db.execute('DELETE FROM platform_leases WHERE token=?', (token,))
                stopped = list(rt.db.execute("SELECT state,result FROM platform_effects WHERE attempt_id=? AND state IN ('cancelled','failed_after_shutdown')", (attempt_id,)))
                if stopped:
                    if not pending:
                        state = 'cancelled' if all(row['state'] == 'cancelled' for row in stopped) else 'failed_after_shutdown'
                    artifacts = {a['id']: a for row in stopped for a in json.loads(row['result']).get('partialArtifacts', [])}
                    attempt['result'] = self._partial_result(list(artifacts.values()))
                preserved_selection = bool(attempt.get('result', {}).pop('verificationPending', False))
                if preserved_selection:
                    attempt['result']['quality']['reasons'] = ['独立复测未完成验收；已完成的候选采集单独保留']
                if (verify_request is not None and verification is None and isinstance(preliminary, dict)
                        and preliminary.get('dataKind') == 'real' and not preliminary.get('partial')
                        and rt.db.execute("SELECT 1 FROM platform_effects WHERE operation_id=? AND state='completed'",
                                          (request['operationId'],)).fetchone()):
                    if not attempt.get('result'):
                        attempt['result'] = {'dataKind': 'real', 'partial': True, 'metrics': {}, 'artifacts': [],
                            'quality': {'status': 'unverified', 'reasons': ['独立复测尚未完成；已完成的候选采集单独保留']}}
                    attempt['result']['selectionResult'] = deepcopy(preliminary)
                    preserved_selection = True
                if verify_request is not None and verification is not None:
                    effect = rt.db.execute("SELECT * FROM platform_effects WHERE operation_id=? AND workflow_id=? AND attempt_id=? AND state='completed'",
                        (verify_request['operationId'], workflow_id, attempt_id)).fetchone()
                    if effect:
                        recovered = json.loads(effect['result'])
                        selection = self._completed_selection_for_reconciliation(rt, workflow_id, attempt, effect)
                        if (isinstance(recovered, dict) and recovered == verification and selection is not None
                                and recovered.get('dataKind') == 'real' and not recovered.get('partial')
                                and recovered.get('acquiredAt') and recovered.get('artifacts')):
                            recovered['quality'] = {'status': 'unverified',
                                'reasons': ['独立复测采集已完成，但验收或提交失败：' + str(exc)]}
                            recovered.pop('verificationPending', None)
                            recovered['selectionResult'] = selection
                            attempt['result'] = recovered
                            attempt['verificationOperationId'] = verify_request['operationId']
                            preserved_selection = True
                attempt.update(state=state, endedAt=now(), error=str(exc))
                self.store.attempt(rt, workflow_id, attempt)
                node = self._node(current, node_id)
                node.update(status='attention', engineState=state, detail=str(exc))
                if stopped or preserved_selection:
                    node['result'] = deepcopy(attempt['result'])
                current.update(status='attention', engineState=state)
                self.store.save(rt, current, 'reconciliation_required' if pending else 'attention', {'message': str(exc)})
                if not pending and current.get('cancelRequested'):
                    self._finish_cancel_locked(rt, current)
            return False

    @staticmethod
    def _partial_result(artifacts):
        return {'dataKind': 'real', 'partial': True, 'metrics': {}, 'artifacts': artifacts,
                'quality': {'status': 'unverified', 'reasons': ['采集已停止；部分数据保留，未完成独立验证']}}

    @staticmethod
    def _completed_selection_for_reconciliation(rt, workflow_id, attempt, effect):
        """Retain only the registered, complete first acquisition of this attempt."""
        attempt_id = attempt.get('id')
        if (not isinstance(attempt_id, str) or effect['attempt_id'] != attempt_id
                or effect['workflow_id'] != workflow_id or effect['operation_id'] != attempt_id + '/verify'
                or attempt.get('operationId') != attempt_id + '/measure'):
            return None
        selection = (attempt.get('result') or {}).get('selectionResult')
        if (not isinstance(selection, dict) or selection.get('dataKind') != 'real'
                or selection.get('partial') or 'selectionResult' in selection
                or selection.get('quality', {}).get('status') != 'unverified'):
            return None
        row = rt.db.execute("SELECT result FROM platform_effects WHERE operation_id=? AND workflow_id=? AND attempt_id=? AND state='completed'",
            (attempt['operationId'], workflow_id, attempt_id)).fetchone()
        if not row:
            return None
        try:
            source = json.loads(row[0])
        except (ValueError, TypeError):
            return None
        if (not isinstance(source, dict) or source.get('dataKind') != 'real' or source.get('partial')
                or not source.get('acquiredAt') or not source.get('artifacts')
                or any(selection.get(key) != source.get(key) for key in
                       ('acquiredAt', 'parameters', 'metrics', 'series', 'artifacts'))):
            return None
        return deepcopy(selection)

    def _call_adapter(self, request, lease_token):
        def emit(event):
            with self.store.transaction() as rt:
                lease = rt.db.execute('SELECT token FROM platform_leases WHERE attempt_id=?', (request['attemptId'],)).fetchone()
                if not lease or lease[0] != lease_token:
                    raise APIError('LEASE_LOST', '设备执行租约不匹配')
                wf = self.get(request['runId'], rt)
                node = self._node(wf, request['nodeId'])
                previous = node.get('telemetry', {})
                if previous.get('controllerOperationId', previous.get('operationId')) != request['operationId']:
                    previous = {}
                    node['progress'] = 0
                # Bind the callback without changing an adapter's own event ID.
                event = {**event, 'controllerOperationId': request['operationId'],
                         'controllerAttemptId': request['attemptId']}
                progress = event.get('progress')
                if number(progress):
                    node['progress'] = max(0, min(99, progress))
                node['telemetry'] = {**previous, **event}
                self.store.save(rt, wf, 'worker_progress', {'nodeId': node['id'], 'message': str(event.get('message') or event.get('event') or '采集进度'), 'telemetry': event})
        try:
            if self._physical_adapter:
                wf = self.get(request['runId'])
                self._assert_adapter_binding(wf)
                if not (wf.get('hardwareConfirmed') or self.profile.get('connectionConfirmedAt')):
                    raise APIError('HARDWARE_HANDOFF_REQUIRED', '本次任务尚未确认设备独占交接，未启动采集', 409)
                ownership = self.hardware_ownership(refresh=True)
                if not ownership['verified'] or ownership['owners']:
                    raise APIError('DEVICE_IN_USE', '设备仍由其他程序连接，未启动本次采集', 409)
                # This instance is scoped to an accepted, exclusive run. Do not
                # persist an enabled flag that silently arms future workflows.
                execution_adapter = self.adapter.with_execution_enabled()
                if getattr(execution_adapter, 'physical_hardware', None) is not True or execution_adapter.profile.get('enabled') is not True:
                    raise APIError('ADAPTER_CONTRACT_REQUIRED', '执行器未提供有效的任务作用域启用实例')
                expected = {k: v for k, v in self.profile.items() if k != 'enabled'}
                actual = {k: v for k, v in execution_adapter.profile.items() if k != 'enabled'}
                if actual != expected:
                    raise APIError('ADAPTER_SCOPE_CHANGED', '任务作用域启用不得更换设备配置')
            else:
                execution_adapter = self.adapter
            if hasattr(execution_adapter, 'build_plan'):
                estimate = execution_adapter.build_plan(request).get('estimated_total_shots')
                if not isinstance(estimate, int) or estimate <= 0:
                    raise APIError('INVALID_BUDGET', '执行器没有提供可核查的采集预算')
                with self.store.transaction() as rt:
                    wf = self.get(request['runId'], rt)
                    if request.get('rbDiagnostic'):
                        self._assert_rb_diagnostic_dispatch(rt, wf, self._node(wf, request['nodeId']),
                            request['rbDiagnostic']['id'], attempt_id=request['attemptId'])
                    if wf.get('reservedShots', 0) + estimate > wf.get('maxTotalShots', 8000000):
                        raise APIError('TOTAL_SHOTS_BUDGET', '本任务累计采集预算不足，未下发本次采集')
                    wf['reservedShots'] = wf.get('reservedShots', 0) + estimate
                    self.store.save(rt, wf, 'budget_reserved', {'message': f'本次预留 {estimate} shots；未知执行不返还预算',
                                                             'operationId': request['operationId'], 'shots': estimate})
            result = execution_adapter.execute(request, emit, lambda: self._cancel_requested(request['runId']))
        except Exception:
            receipt = self.adapter.reconcile(request['operationId'])
            with self.store.transaction() as rt:
                status = receipt.get('status')
                if status in ('not_started', 'rejected', 'failed_before_start'):
                    rt.db.execute("UPDATE platform_effects SET state='not_started',result=? WHERE operation_id=?", (dumps(receipt), request['operationId']))
                elif status in ('cancelled', 'failed_after_shutdown') and receipt.get('shutdownConfirmed') is True:
                    receipt['partialArtifacts'] = [self._artifact(rt, artifact) for artifact in receipt.get('partialArtifacts', [])]
                    rt.db.execute('UPDATE platform_effects SET state=?,result=? WHERE operation_id=?', (status, dumps(receipt), request['operationId']))
                    record_effect(rt.db, request['operationId'], self.root)
                else:
                    rt.db.execute("UPDATE platform_effects SET state='unknown',result=? WHERE operation_id=?", (dumps(receipt), request['operationId']))
            raise
        with self.store.transaction() as rt:
            result['artifacts'] = [self._artifact(rt, a) for a in result.get('artifacts', [])]
            rt.db.execute("UPDATE platform_effects SET state='completed',result=? WHERE operation_id=?", (dumps(result), request['operationId']))
            record_effect(rt.db, request['operationId'], self.root)
            rt._event(request['runId'], 'effect_completed', {'operationId': request['operationId'], 'attemptId': request['attemptId']})
        return result

    def _validation_snapshot(self, rt, wf, node, parameters, versions):
        accepted = []
        keys = self.adapter.validation_parameter_keys() if callable(getattr(self.adapter, 'validation_parameter_keys', None)) else CALIBRATION_KEYS
        for prior in wf['nodes']:
            if prior['resourceId'] != node['resourceId'] or prior['engineState'] != 'verified':
                continue
            if prior['procedureId'] not in ('sq_rabi_amplitude', 'sq_rabi_time', 'ro_iq01', 'ro_optimize'):
                continue
            row = rt.db.execute('SELECT payload FROM platform_attempts WHERE id=?', (prior.get('latestAttemptId'),)).fetchone()
            if not row:
                continue
            attempt = json.loads(row[0])
            if not attempt.get('verificationOperationId') or attempt.get('result', {}).get('quality', {}).get('status') != 'passed':
                continue
            # A later calibration may change the working point without deleting
            # an earlier successful diagnostic. Only current independently
            # verified evidence belongs in a downstream calibration snapshot.
            result = attempt['result']
            actual = result.get('parameters', {})
            if result.get('stale') or any(k not in actual or k not in parameters or not self._same_parameter(actual[k], parameters[k]) for k in keys):
                continue
            accepted.append({'procedureId': prior['procedureId'], 'status': 'passed',
                             'selectionOperationId': attempt['operationId'], 'verificationOperationId': attempt['verificationOperationId'],
                             'ruleVersion': wf['gatesVersion']})
        return {'version': 1, 'runId': wf['id'], 'resourceId': node['resourceId'], 'parameters': parameters,
                'parameterVersions': versions, 'acceptedEvidence': accepted}

    def _rb_prerequisites_current(self, workflow_id, node_id):
        if not self._physical_adapter:
            return True
        keys = self.adapter.validation_parameter_keys() if callable(getattr(self.adapter, 'validation_parameter_keys', None)) else CALIBRATION_KEYS
        with self.store.transaction() as rt:
            wf = self.get(workflow_id, rt)
            node = self._node(wf, node_id)
            parameters, versions = self._parameters(rt, wf, node)
            for procedures, fallback in ((('sq_rabi_amplitude', 'sq_rabi_time'), 's03_rabi_amplitude'), (('ro_iq01',), 's04_readout_fidelity')):
                candidates = [n for n in wf['nodes'] if n['resourceId'] == node['resourceId'] and n['procedureId'] in procedures]
                matched = False
                for prior in candidates:
                    actual = prior.get('result', {}).get('parameters', {})
                    mismatch = [k for k in keys if k not in actual or k not in parameters or not self._same_parameter(actual[k], parameters[k])]
                    if prior['engineState'] == 'verified' and not mismatch:
                        matched = True
                        break
                if not matched:
                    prior = self._node(wf, node['resourceId'] + ':' + fallback)
                    actual = prior.get('result', {}).get('parameters', {})
                    mismatch = [k for k in keys if k not in actual or k not in parameters or not self._same_parameter(actual[k], parameters[k])]
                    affected = self._invalidate(wf, prior['id'], include_target=True)
                    wf['status'], wf['engineState'], wf['currentNodeId'] = 'queued', 'backtrack_queued', prior['id']
                    self._record_decision(rt, wf, node_id, {'action': 'backtrack', 'reason': 'RB 前置校准与当前工作点不同，需要在当前参数下重新验证：' + ','.join(mismatch),
                                                         'evidenceIds': [], 'ruleId': 'field.readout_change', 'targetNodeId': prior['id'], 'invalidatedNodes': affected}, 'rule')
                    self.store.save(rt, wf, 'backtrack', {'message': 'RB 前置检查返回 ' + prior['label']})
                    return False
        return True

    def _cancel_requested(self, workflow_id):
        return self.stop_event.is_set() or bool(self.get(workflow_id).get('cancelRequested'))

    def _calibration_context_reasons(self, rt, wf, node, result):
        if not self._physical_adapter:
            return []
        keys = self.adapter.validation_parameter_keys() if callable(getattr(self.adapter, 'validation_parameter_keys', None)) else CALIBRATION_KEYS
        actual = result.get('parameters')
        actual = actual if isinstance(actual, dict) else {}
        committed, _ = self._parameters(rt, wf, {**node, 'parameterOverrides': {}})
        missing = [k for k in keys if k not in actual or k not in committed]
        if missing:
            return ['实际采集回执缺少完整校准上下文，不能验收：' + ', '.join(missing)]
        outputs = {PARAMETER_ALIASES.get(k, k) for k in PARAMETERS.get(node['procedureId'], ([], []))[1]}
        changed = [k for k in keys if k not in outputs and not self._same_parameter(actual[k], committed[k])]
        if changed:
            detail = ', '.join(f'{k}: {committed[k]} → {actual[k]}' for k in changed)
            return ['本轮实测工作点与已提交校准不同，当前方法不能提交这些上下文参数；请回到相应上游校准验证：' + detail]
        return []

    def _commit_candidate(self, rt, wf, node, candidate, versions):
        prefix = wf['id'] + ':' + node['resourceId'] + ':'
        changed = []
        changed_readout = []
        baseline, _ = self._parameters(rt, wf, {**node, 'parameterOverrides': {}})
        allowed = {PARAMETER_ALIASES.get(k, k) for k in PARAMETERS.get(node['procedureId'], ([], []))[1]}
        for key, value in candidate.items():
            key = PARAMETER_ALIASES.get(key, key)
            if key not in allowed or not number(value):
                continue
            row = rt.db.execute('SELECT revision,value FROM parameters WHERE name=?', (prefix + key,)).fetchone()
            version = row['revision'] if row else 0
            if version != versions.get(key, 0):
                raise APIError('STALE_REVISION', '候选基准已变化，不能提交')
            if row and json.loads(row['value']) == value:
                continue
            previous = json.loads(row['value']) if row else baseline.get(key)
            if key in ('readout_hz', 'readout_amp') and not self._same_parameter(previous, value):
                changed_readout.append(key)
            rt.db.execute('INSERT OR REPLACE INTO parameters VALUES(?,?,?)', (prefix + key, dumps(value), version + 1))
            node['parameterVersions'][key] = version + 1
            changed.append(prefix + key)
        if changed:
            rt._invalidate(changed, exclude=wf['id'] + '/' + node['id'])
            affected = self._invalidate(wf, node['id'], include_target=False)
            self._readout_revalidation(rt, wf, node)
            rebuild_target = None
            if node['procedureId'] == 'ro_optimize' and changed_readout:
                rebuild_target = self._start_readout_revalidation(rt, wf, node)
                affected = list(dict.fromkeys(affected + [n['id'] for n in wf['nodes'] if n['resourceId'] == node['resourceId'] and n['engineState'] == 'stale']))
            self._record_decision(rt, wf, node['id'], {'action': 'commit', 'reason': '独立复测通过后提交候选版本',
                'evidenceIds': [a['id'] for a in node['result']['artifacts']], 'parameterChanges': candidate,
                'invalidatedNodes': affected}, 'rule')
            return rebuild_target

    @staticmethod
    def _repeatability(selection, verification, parameters):
        reasons = []
        for key, selected in selection.items():
            repeated = verification.get(key)
            if not number(selected) or not number(repeated):
                reasons.append('独立复测缺少同一候选参数：' + key)
                continue
            if key in ('readout_hz', 'drive_hz'):
                span = parameters.get('span_hz', 4e6 if key == 'readout_hz' else 20e6)
                tolerance = max(5e4, 2 * span / max(1, parameters.get('points', 81) - 1))
            else:
                tolerance = max(abs(selected) * .15, 1e-12)
            if abs(repeated - selected) > tolerance:
                reasons.append(f'候选重复性不足：{key} 从 {selected:g} 变为 {repeated:g}，容差 {tolerance:g}')
        return reasons

    @staticmethod
    def _invalidate(wf, node_id, include_target=False):
        affected = {node_id}
        while True:
            expanded = affected | {n['id'] for n in wf['nodes'] if set(n['dependsOn']) & affected}
            if expanded == affected:
                break
            affected = expanded
        if not include_target:
            affected.discard(node_id)
        diagnostic = wf.get('rbDiagnostic') or {}
        if diagnostic and affected:
            diagnostic.update(state='invalidated', finishedAt=now(), resultStatus='unverified')
        for node in wf['nodes']:
            if node['id'] in affected:
                node['status'], node['engineState'], node['progress'], node['mainProcedureComplete'] = 'pending', 'stale', 0, False
                node['detail'] = '前置参数已变更，历史结果保留，等待重新测量'
                if node.get('result'):
                    node['result']['stale'] = True
        return sorted(affected)

    def _record_decision(self, rt, wf, node_id, decision, source):
        record = {**decision, 'id': ident('decision'), 'nodeId': node_id, 'createdAt': now(), 'source': source,
                  'attemptId': self._node(wf, node_id).get('latestAttemptId')}
        if decision.get('action') == 'backtrack' and decision.get('targetNodeId'):
            wf['edges'] = [edge for edge in wf['edges'] if not (edge.get('kind') == 'backtrack' and edge['from'] == node_id and edge['to'] == decision['targetNodeId'])]
            wf['edges'].append({'from': node_id, 'to': decision['targetNodeId'], 'kind': 'backtrack', 'decisionId': record['id']})
        self.store.decision(rt, wf['id'], record)
        return record

    def _bounds(self):
        return {k: (deepcopy(v) if isinstance(v, dict) else {'min': v[0], 'max': v[1],
                    **({'integer': True} if k in ('shots', 'capture_repeats', 'points', 'pi_pulses') else {})})
                for k, v in self.profile.get('parameterBounds', {}).items()}

    def _parameter_schema(self, procedure):
        hook = getattr(self.adapter, 'parameter_schema', None)
        return deepcopy(hook(procedure)) if callable(hook) else self._bounds()

    def _normalize_candidate(self, procedure, candidate):
        hook = getattr(self.adapter, 'normalize_candidate', None)
        return deepcopy(hook(procedure, candidate)) if callable(hook) else deepcopy(candidate)

    def _validated_candidate(self, procedure, result, stage):
        """Reject unusable fit proposals without discarding their acquired evidence.

        Only expected proposal validation failures are scientific gate failures.
        Instrument, adapter, and normalization implementation exceptions still
        follow the execution/reconciliation error path.
        """
        raw = deepcopy(result.get('candidateParameters', {}))
        normalized = None
        schema = self._bounds()

        def validate(candidate):
            if not isinstance(candidate, dict) or any(not isinstance(k, str) for k in candidate):
                raise APIError('INVALID_CANDIDATE_PARAMETERS', '拟合候选参数必须是字符串键的对象')
            for key, value in candidate.items():
                try:
                    self._validate_parameter(key, value, schema)
                except APIError as exc:
                    if exc.code not in ('UNKNOWN_PARAMETER', 'PARAMETER_OUT_OF_RANGE'):
                        raise
                    raise APIError(exc.code, f'{exc.message}：{key}={value!r}；允许范围 {schema.get(key)}') from exc

        try:
            validate(raw)
            normalized = self._normalize_candidate(procedure, deepcopy(raw))
            validate(normalized)
            return normalized
        except APIError as exc:
            if exc.code not in ('INVALID_CANDIDATE_PARAMETERS', 'UNKNOWN_PARAMETER', 'PARAMETER_OUT_OF_RANGE'):
                raise
            result['quality']['status'] = 'failed'
            result['quality']['reasons'].append('拟合候选未通过参数校验：' + str(exc))
            result['candidateValidation'] = {'accepted': False, 'stage': stage,
                'raw': raw, 'normalized': normalized, 'code': exc.code, 'message': str(exc)}
            return None

    def _normalize_changes(self, procedure, changes, parameters):
        hook = getattr(self.adapter, 'normalize_parameter_changes', None)
        result = hook(procedure, deepcopy(changes), deepcopy(parameters)) if callable(hook) else changes
        if not isinstance(result, dict):
            raise APIError('INVALID_PARAMETER_NORMALIZATION', '执行器参数规范化必须返回键值对象')
        return deepcopy(result)

    @staticmethod
    def _validate_parameter(key, value, schema):
        rule = schema.get(key)
        if not isinstance(rule, dict):
            raise APIError('UNKNOWN_PARAMETER', '当前方法不允许调整参数：' + key)
        if 'enum' in rule:
            if value not in rule['enum'] or isinstance(value, (dict, list)):
                raise APIError('PARAMETER_OUT_OF_RANGE', key + ' 不在允许选项内')
        elif not number(value) or not rule['min'] <= value <= rule['max'] or (rule.get('integer') and int(value) != value):
            raise APIError('PARAMETER_OUT_OF_RANGE', key + ' 超出允许范围或类型')

    @staticmethod
    def _same_parameter(left, right):
        if number(left) and number(right):
            return abs(left - right) <= max(abs(right) * 1e-12, 1e-12)
        return type(left) is type(right) and left == right

    def _assert_adapter_binding(self, wf):
        if wf['mode'] == 'real' and (wf.get('adapterId') != self.adapter_id or wf.get('adapterProfileHash') != self.adapter_profile_hash
                                    or self._adapter_fingerprint() != self.adapter_profile_hash):
            raise APIError('ADAPTER_CHANGED', '任务绑定的执行器或配置已变化，请创建与当前配置绑定的新任务', 409)

    def _adapter_fingerprint(self):
        profile = {k: v for k, v in self.profile.items() if k not in ('enabled', 'connectionConfirmedAt')}
        identity = {'profile': profile}
        if self._physical_adapter and self.profile.get('sourceManifest'):
            root = checked_path(getattr(self.adapter, 'root', self.root))
            manifest = checked_path(root / self.profile['sourceManifest'], parent=root)
            if not manifest.is_file():
                raise APIError('ADAPTER_MANIFEST_MISSING', '执行器源码清单不存在，不能冻结当前任务')
            identity['sourceManifestSha256'] = file_hash(manifest)
        return digest(identity)

    def _brain(self, path, body=None, timeout=180):
        if path == '/decide' and javascript_json_units_upper_bound(body) > MODEL_INPUT_MAX_UNITS:
            return {'status': 'waiting_model', 'error': {'code': 'context_budget_exceeded', 'source': 'controller_preflight'},
                    'message': '模型上下文仍超出输入预算；已保留完整采集记录，未发送模型请求。'}
        data = None if body is None else dumps(body).encode()
        request = urllib.request.Request(self.brain_url + path, data=data, headers={'Content-Type': 'application/json'})
        # Local services bypass ambient HTTP proxies.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            # Only recognize this local service's bounded, known validation
            # codes. Never echo arbitrary upstream bodies or account details.
            if path == '/decide' and exc.code == 400:
                try:
                    error = json.loads(exc.read(4097)).get('error', {})
                    reason = error.get('message')
                    if error.get('code') == 'invalid_request' and reason in ('input_too_large', 'input_too_deep'):
                        return {'status': 'waiting_model', 'error': {'code': reason, 'source': 'agent_input_validation'},
                            'message': '模型请求未通过本地输入校验：' + reason + '；尚未调用会员模型。'}
                except (ValueError, TypeError, AttributeError):
                    pass
            return {'status': 'waiting_model', 'message': '模型服务当前不可用：' + str(exc), 'configured': False}
        except (OSError, ValueError) as exc:
            return {'status': 'waiting_model', 'message': '模型服务当前不可用：' + str(exc), 'configured': False}

    def _model_readout_rescan_context(self, rt, wf, node, effective_parameters):
        """Read-only attestation for model bounds; never release or mutate a pin."""
        marker = wf.get('readoutRevalidations', {}).get(node['resourceId'])
        context = {'state': None, 'eligible': False, 'reason': '当前没有固定读出重建'}
        if node['procedureId'] != 'ro_optimize' or not isinstance(marker, dict) or marker.get('state') not in ('active', 'fixed_failed'):
            return context
        context.update(state=marker['state'], reason='固定候选尚未满足当前失败尝试及工作点核验；保留采样参数，未开放重扫')
        if (marker.get('state') != 'fixed_failed' or marker.get('optimizerNodeId') != node['id']
                or wf.get('currentNodeId') != node['id'] or not marker.get('id')):
            return context
        values, versions = marker.get('values'), marker.get('parameterVersions')
        committed, current_versions = self._parameters(rt, wf, {**node, 'parameterOverrides': {}})
        keys = ('readout_hz', 'readout_amp')
        if (not isinstance(values, dict) or not isinstance(versions, dict)
                or any(k not in values or not number(values[k]) or type(versions.get(k)) is not int for k in keys)
                or any(not self._same_parameter(value, committed.get(k))
                       or not self._same_parameter(value, effective_parameters.get(k))
                       or versions.get(k) != current_versions.get(k, 0) for k, value in values.items())):
            return context
        result = node.get('result') or {}
        attempt_id = node.get('latestAttemptId')
        actual = result.get('parameters') or {}
        if (not isinstance(attempt_id, str) or not attempt_id or marker.get('lastFixedAttemptId') != attempt_id
                or result.get('dataKind') != 'real' or result.get('partial') or result.get('stale')
                or result.get('quality', {}).get('status') != 'failed'
                or not isinstance(actual, dict)
                or any(not self._same_parameter(actual.get(k), values[k]) for k in keys)
                or actual.get('amplitudes') != [values['readout_amp']]):
            return context
        row = rt.db.execute('SELECT payload FROM platform_attempts WHERE id=? AND workflow_id=? AND node_id=?',
                            (attempt_id, wf['id'], node['id'])).fetchone()
        if not row:
            return context
        try:
            attempt = json.loads(row['payload'])
        except (TypeError, ValueError):
            return context
        if not isinstance(attempt, dict):
            return context
        recorded_marker = attempt.get('readoutRevalidation') or {}
        if (attempt.get('mode') != 'real' or attempt.get('procedureId') != node['procedureId']
                or attempt.get('state') != 'attention' or not attempt.get('endedAt') or attempt.get('result') != result
                or not isinstance(recorded_marker, dict)
                or recorded_marker.get('id') != marker['id'] or recorded_marker.get('values') != values
                or recorded_marker.get('parameterVersions') != versions
                or any(attempt.get('parameterVersions', {}).get(k, 0) != current_versions.get(k, 0) for k in keys)):
            return context
        effects = list(rt.db.execute('SELECT operation_id,state,request,result FROM platform_effects WHERE workflow_id=? AND attempt_id=?',
                                    (wf['id'], attempt_id)))
        if (not isinstance(attempt.get('operationId'), str) or not attempt['operationId']
                or (attempt.get('verificationOperationId') is not None
                    and not isinstance(attempt['verificationOperationId'], str))):
            return context
        expected_operations = {attempt['operationId']}
        if attempt.get('verificationOperationId'):
            expected_operations.add(attempt['verificationOperationId'])
        if (not effects or any(e['state'] != 'completed' for e in effects) or self._has_lease(rt, wf['id'])
                or not all(isinstance(op, str) and op for op in expected_operations)
                or {e['operation_id'] for e in effects} != expected_operations
                or rt.db.execute("SELECT 1 FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')", (wf['id'],)).fetchone()):
            return context
        for effect in effects:
            try:
                request, receipt = json.loads(effect['request']), json.loads(effect['result'])
            except (TypeError, ValueError):
                return context
            if not isinstance(request, dict) or not isinstance(receipt, dict):
                return context
            if (any(request.get(k) != value for k, value in {'runId': wf['id'], 'nodeId': node['id'],
                    'resourceId': node['resourceId'], 'procedureId': node['procedureId'], 'attemptId': attempt_id,
                    'operationId': effect['operation_id']}.items()) or receipt.get('dataKind') != 'real' or receipt.get('partial')
                    or not isinstance(request.get('readoutRevalidation'), dict)
                    or request['readoutRevalidation'].get('id') != marker['id']):
                return context
            for evidence_parameters in (request.get('parameters'), receipt.get('parameters')):
                if (not isinstance(evidence_parameters, dict)
                        or any(not self._same_parameter(evidence_parameters.get(k), values[k]) for k in keys)
                        or evidence_parameters.get('amplitudes') != [values['readout_amp']]):
                    return context
        context.update(eligible=True, reason='当前固定点失败记录、实际工作点与已提交读出版本一致；只允许明确成对范围请求，仍需原控制器和计划复核')
        return context

    def _decision_execution_constraints(self, wf, node, payload):
        """Explain existing limits and saved scan geometry; never plan or acquire."""
        constraints = {
            'maximumTotalShots': self.profile.get('maximumTotalShots'),
            'maximumPoints': self.profile.get('maximumPoints'),
            'scope': 'per_operation',
            'maximumWorkflowShots': wf['maxTotalShots'],
            'reservedWorkflowShots': wf.get('reservedShots', 0),
            'remainingWorkflowShots': max(0, wf['maxTotalShots'] - wf.get('reservedShots', 0)),
            'selectionAndVerificationBudgetedSeparately': True,
            'selectionAdmissionRequiresVerificationCapacity': self._physical_adapter,
            'budgetSemantics': 'maximumTotalShots 和 maximumPoints 分别限制一次原生操作。启动 selection 前必须有首测与后续独立 verification 的联合容量；该预检不扣预算，实际每个 operation 仍只预留一次。verification 只检查自身剩额，不再为首测计费；失败或取消不退还已预留预算。'}
        adapter_type = type(self.adapter)
        semantics = sampling_context(implementation_id=adapter_type.__module__ + '.' + adapter_type.__qualname__,
            profile_id=self.profile.get('id'), procedure_id=node['procedureId'])
        if semantics:
            constraints['samplingSemantics'] = semantics
        if node['procedureId'] != 'ro_power_shift':
            return constraints
        constraints['scanSemantics'] = {
            'formulaShotsPerOperation': 'frequencyPoints * powerPoints * capture_repeats * acquisitions_per_point * shots',
            'formulaPointsPerOperation': 'frequencyPoints * powerPoints * capture_repeats',
            'powerAxis': '当前 s21_power 使用已记录的 powers 数组；未显式提供时原生默认 [0.1,0.2,0.4,0.6,0.8]。',
            'ineffectivePowerAxisParameters': ['amp_start', 'amp_stop', 'readout_amp'],
            'notice': 'amp_start/amp_stop/readout_amp 不改变此方法的固定功率扫描轴，不能据此删去 0.1 档。模型仅可调整 shots、capture_repeats、acquisitions_per_point、points、span_hz；修改 readout_hz 必须回到上游读出定频并重新验证。'}
        result = node.get('result') or {}
        actual = result.get('parameters') or {}
        groups = payload.get('observations', {}).get('powerSeriesDiagnostics', {}).get('groups', [])
        series = [s for s in result.get('series', []) if isinstance(s, dict) and s.get('fit') is not True]
        values = {key: actual.get(key) for key in ('points', 'capture_repeats', 'acquisitions_per_point', 'shots')}
        recorded_powers = result.get('analysisQuality', {}).get('powers_waveform_scale')
        confirmed = (all(type(value) is int and value > 0 for value in values.values()) and bool(groups) and bool(series)
                     and isinstance(recorded_powers, list) and all(number(v) for v in recorded_powers)
                     and sorted(recorded_powers) == [g['powerWaveformScale'] for g in groups]
                     and len(series) == len(groups) * values['capture_repeats']
                     and all(g['captureCount'] == values['capture_repeats'] and g['sameFrequencyGrid'] and g['uniqueCaptureLabels'] for g in groups)
                     and all(len(s.get('x', [])) == values['points'] and len(s.get('y', [])) == values['points'] for s in series))
        constraints['currentScan'] = {'status': 'confirmed' if confirmed else 'unconfirmed',
            'source': '当前 result.parameters、analysisQuality.powers_waveform_scale 与保存的逐 capture series 交叉核对；未新采集或读取原始文件'}
        if confirmed:
            base = values['points'] * len(groups)
            per_capture = base * values['shots']
            total = per_capture * values['capture_repeats'] * values['acquisitions_per_point']
            constraints['currentScan'].update(frequencyPoints=values['points'], powerPoints=len(groups),
                powersWaveformScale=sorted(recorded_powers), pointsPerCapture=base,
                captureRepeats=values['capture_repeats'], acquisitionsPerPoint=values['acquisitions_per_point'],
                shotsPerAcquisition=values['shots'], pointsPerOperation=base * values['capture_repeats'],
                shotsPerOperation=total, selectionPlusVerificationShots=2 * total)
            limit = constraints['maximumTotalShots']
            if type(limit) is int and limit > 0:
                constraints['currentScan']['maximumCaptureAcquisitionProductAtCurrentGridAndShots'] = limit // per_capture
                constraints['illustrativeCostsAtCurrentGridAndShots'] = [
                    {'capture_repeats': captures, 'acquisitions_per_point': acquisitions,
                     'shotsPerOperation': per_capture * captures * acquisitions,
                     'selectionPlusVerificationShots': 2 * per_capture * captures * acquisitions,
                     'withinPerOperationShotLimit': per_capture * captures * acquisitions <= limit}
                    for captures, acquisitions in ((4, 4), (2, 4), (6, 4))]
        return constraints

    def _prior_failure_context(self, rt, wf, node):
        """Read-only archive projection; no adapter execution or DB writes.

        Staged after deployment SHA 6ab47e7785cb68405e8b88d86373d9354818b8e72d6dd7c4e6d4f740a7766a27.
        Root owns checkpoint deployment. Frozen native source/profile is unchanged.
        """
        cases, examined, excluded = [], 0, 0
        try:
            adapter_type = type(self.adapter)
            implementation = adapter_type.__module__ + '.' + adapter_type.__qualname__
            if implementation != 'adapters.arclight.ArcLightAdapter':
                return bounded_context([], wf, status='unsupported_identity')
            root = checked_path(self.adapter.root)
            profiles = {identity: checked_path(root / filename, parent=root).read_bytes()
                        for identity, (filename, _) in PROFILE_FILES.items()}
            manifest = checked_path(root / 'adapters/arclight_runtime/source_manifest.json', parent=root).read_bytes()
            bindings = audited_bindings(implementation, profiles, manifest)
            if (wf.get('adapterId') not in bindings
                    or wf.get('adapterProfileHash') != bindings[wf['adapterId']]['fingerprint']):
                return bounded_context([], wf, status='unaudited_binding')
            rows = rt.db.execute(
                'SELECT a.payload AS attempt_payload,w.payload AS workflow_payload '
                'FROM platform_attempts a JOIN platform_workflows w ON w.id=a.workflow_id '
                'WHERE a.node_id=? ORDER BY a.rowid DESC LIMIT ?', (node['id'], MAX_CANDIDATES))
            # Bounds archive verification I/O as well as prompt size. Large or
            # unverifiable files are excluded, never counted as trusted cases.
            remaining_bytes = 2 * 1024**3
            for row in rows:
                examined += 1
                try:
                    old_wf, attempt = json.loads(row['workflow_payload']), json.loads(row['attempt_payload'])
                    if not eligible_header(old_wf, attempt, wf, node, bindings):
                        excluded += 1
                        continue
                    effects = []
                    files_by_operation = {}
                    for stored in rt.db.execute('SELECT * FROM platform_effects WHERE attempt_id=?', (attempt['id'],)):
                        effect = dict(stored)
                        if effect['state'] != 'completed' or not effect['result']:
                            raise ValueError('incomplete_effect')
                        effect['request'], effect['result'] = json.loads(effect['request']), json.loads(effect['result'])
                        if not isinstance(effect['request'], dict) or not isinstance(effect['result'], dict):
                            raise ValueError('malformed_effect')
                        effects.append(effect)
                        operation = effect['operation_id']
                        op_root = checked_path(root / 'data/runs/arclight_operations' / hashlib.sha256(operation.encode()).hexdigest(), parent=root)
                        checked_files = []
                        inventory = effect['result'].get('artifacts')
                        if not isinstance(inventory, list) or not all(isinstance(a, dict) for a in inventory):
                            raise ValueError('malformed_artifacts')
                        needed = [a for a in inventory
                                  if a.get('kind') == 'raw' or a.get('name') in CHECKED_JSON_NAMES]
                        if not 1 <= len(needed) <= 128:
                            raise ValueError('artifact_inventory_limit')
                        for artifact in needed:
                            registered = rt.db.execute('SELECT * FROM platform_artifacts WHERE id=?', (artifact['id'],)).fetchone()
                            if (not registered or registered['sha256'] != artifact['sha256']
                                    or artifact['id'] != 'real-' + artifact['sha256'][:24]):
                                raise ValueError('artifact_not_registered')
                            registered_payload = json.loads(registered['payload'])
                            if not isinstance(registered_payload, dict) or registered_payload.get('kind') != artifact['kind']:
                                raise ValueError('artifact_kind_mismatch')
                            path = checked_path(artifact['sourcePath'], parent=op_root)
                            size = path.stat().st_size
                            if size > remaining_bytes or (artifact['kind'] != 'raw' and size > 2 * 1024**2):
                                raise ValueError('archive_read_budget')
                            remaining_bytes -= size
                            document_bytes = None
                            if artifact['kind'] != 'raw':
                                with path.open('rb') as stream:
                                    document_bytes = stream.read(2 * 1024**2 + 1)
                            actual_sha = file_hash(path) if document_bytes is None else hashlib.sha256(document_bytes).hexdigest()
                            if (document_bytes is not None and len(document_bytes) > 2 * 1024**2
                                    or path.name != artifact['name'] or actual_sha != registered['sha256']):
                                raise ValueError('artifact_hash_mismatch')
                            entry = {'artifactRef': artifact['id'], 'sha256': artifact['sha256'],
                                     'name': artifact['name'], 'kind': artifact['kind'],
                                     'folder': path.parent.relative_to(op_root).as_posix(), 'registryHashVerified': True}
                            if artifact['kind'] != 'raw':
                                entry['document'] = json.loads(document_bytes.decode('utf-8-sig'))
                            checked_files.append(entry)
                        files_by_operation[operation] = checked_files
                    case = project_failure_case({'workflow': old_wf, 'attempt': attempt, 'effects': effects,
                        'files': files_by_operation}, wf, node, bindings)
                    if case is None:
                        excluded += 1
                    else:
                        cases.append(case)
                    if len(cases) >= MAX_CASES:
                        break
                except (APIError, OSError, ValueError, TypeError, KeyError):
                    excluded += 1
            return bounded_context(cases, wf, examined=examined, excluded=excluded)
        except (APIError, OSError, ValueError, TypeError, KeyError):
            return bounded_context([], wf, examined=examined, excluded=excluded, status='archive_unavailable')

    def _decide(self, workflow_id, node_id):
        wf = self.get(workflow_id)
        node = self._node(wf, node_id)
        if wf['policy'] != 'autopilot':
            self._attention(workflow_id, 'quality_attention', '本轮结果待处理；可查看质量依据后复测或选择自主策略')
            return False
        try:
            self._assert_adapter_binding(wf)
        except APIError as exc:
            self._attention(workflow_id, 'adapter_changed', exc.message, node_id=node_id)
            return False
        self._attention(workflow_id, 'deciding', '智能体正在根据实测证据选择下一步')
        wf = self.get(workflow_id)
        node = self._node(wf, node_id)
        with self.store.connect() as rt:
            effective_parameters, _ = self._parameters(rt, wf, node)
            rescan_context = self._model_readout_rescan_context(rt, wf, node, effective_parameters)
            prior_failure_context = self._prior_failure_context(rt, wf, node)
        attempts, history = self.store.detail(workflow_id, node_id)
        backtrack_admission = eligible_backtracks(wf, self._ancestors(wf, node))
        payload = {'runId': workflow_id, 'nodeId': node_id, 'revision': wf['revision'], 'mode': 'real',
                   'goal': wf['name'], 'observations': {'result': node.get('result'), 'parameters': effective_parameters,
                   'attempt': node['attempt'], 'remainingAttempts': wf['maxAttemptsPerNode'] - node['attempt']},
                   'allowedActions': ['retry', 'backtrack', 'wait', 'stop'], 'allowedBacktrackTargets': backtrack_admission['allowed'],
                   'backtrackAdmission': backtrack_admission,
                   'parameterBounds': self._parameter_schema(node['procedureId']), 'rules': node['rules'], 'history': history[-8:]}
        physical_parameter_bounds = deepcopy(payload['parameterBounds'])
        marker = wf.get('readoutRevalidations', {}).get(node['resourceId'])
        if marker:
            payload['observations']['readoutRevalidation'] = {**deepcopy(marker),
                'retrySemantics': '仅改 shots 等采集参数保持固定读出候选。当前优化节点固定点验证失败后，retry 同时指定 amp_start/amp_stop 才申请重新全扫；预算不增加。'}
            if marker['state'] in ('active', 'fixed_failed') and node_id in marker.get('lockedNodeIds', [marker['optimizerNodeId']]):
                for key, value in marker['values'].items():
                    payload['parameterBounds'][key] = {'min': value, 'max': value}
        payload = build_decision_context(payload, procedure_id=node['procedureId'])
        # Separate from observations/history: historical IDs can never become
        # the current attempt's evidence allowlist, including in legacy agents.
        if node['procedureId'] == 'ro_power_shift':
            allowed = {'shots', 'capture_repeats', 'acquisitions_per_point', 'points', 'span_hz'}
            payload['parameterBounds'] = {k: v for k, v in payload['parameterBounds'].items() if k in allowed}
        adapter_type = type(self.adapter)
        semantics = build_model_parameter_contract(
            implementation_id=adapter_type.__module__ + '.' + adapter_type.__qualname__,
            profile_id=self.profile.get('id'), procedure_id=node['procedureId'],
            parameter_bounds=physical_parameter_bounds, effective_parameters=effective_parameters,
            fixed_readout_state=rescan_context['state'], fixed_rescan_eligible=rescan_context['eligible'])
        if semantics['applied']:
            payload['parameterBounds'] = deepcopy(semantics['parameterBounds'])
            payload['modelParameterSemantics'] = semantics
            if rescan_context['state'] is not None:
                payload['modelParameterSemantics']['fixedReadoutContext'] = rescan_context
        decision_bounds = deepcopy(payload['parameterBounds'])
        semantic_conditions = deepcopy(semantics['conditions']) if semantics['applied'] else []
        payload['executionConstraints'] = self._decision_execution_constraints(wf, node, payload)
        payload = attach_prior_context(payload, prior_failure_context)
        payload = attach_curve_summary(payload, procedure_id=node['procedureId'])
        payload = project_model_input(payload)
        if node['procedureId'] in CAPTURE_DIAGNOSTIC_PROCEDURES:
            sources = resolve_sources(self.root, self.store.path, wf, node,
                implementation_id=adapter_type.__module__ + '.' + adapter_type.__qualname__)
            payload = attach_diagnostics(payload, calculate_diagnostics(sources))
        upstream_context = resolve_accepted_upstream(self.root, self.store.path, wf, node,
            implementation_id=adapter_type.__module__ + '.' + adapter_type.__qualname__)
        payload = attach_accepted_upstream(payload, upstream_context)
        response = self._brain('/decide', payload)
        with self.store.transaction() as rt:
            current = self.get(workflow_id, rt)
            if current['revision'] != payload['revision']:
                return False  # A user action superseded this model call.
            current_node = self._node(current, node_id)
            if not isinstance(response, dict) or response.get('status') != 'ok':
                message = str(response.get('message') or response.get('error') or '模型尚未返回有效决策') if isinstance(response, dict) else '模型尚未返回有效决策'
                current['status'], current['engineState'] = 'attention', 'waiting_model'
                current_node.update(status='attention', engineState='waiting_model', detail=message)
                self.store.save(rt, current, 'attention', {'message': message})
                return False
            try:
                binding = response.get('binding')
                if binding and binding != {k: payload[k] for k in ('runId', 'nodeId', 'revision')}:
                    raise APIError('STALE_MODEL_DECISION', '模型响应绑定了不同运行版本')
                decision = validate_proposal(response.get('decision'), current, current_node, decision_bounds)
                release_readout = None
                if decision['action'] == 'retry':
                    requested_changes = {k: v.get('to') if isinstance(v, dict) else v for k, v in decision.get('parameterChanges', {}).items()}
                    parameters, versions = self._parameters(rt, current, current_node)
                    changes = self._normalize_changes(current_node['procedureId'], requested_changes, parameters)
                    for condition in semantic_conditions:
                        if (condition.get('requiresCallerSemanticCheck') and set(condition['parameters']).intersection(changes)
                                and not set(condition.get('requires', [])).issubset(changes)):
                            raise APIError('INEFFECTIVE_PARAMETER_CHANGE', '固定读出点的 points 只有与 amp_start、amp_stop 成对重扫请求同时提供才生效')
                    for key, value in changes.items():
                        self._validate_parameter(key, value, self._parameter_schema(current_node['procedureId']))
                    trial, release_readout = self._readout_retry_parameters(rt, current, current_node, parameters, versions, changes)
                    lookahead = self._validate_plan(current, current_node, trial, versions, ident('model-plan'),
                                                    joint_budget=True, rt=rt)
                    if lookahead is not None:
                        decision['budgetLookahead'] = lookahead
                    decision['parameterChanges'] = changes
                    if release_readout:
                        decision['readoutRevalidation'] = {'id': release_readout['id'], 'mode': 'full_scan',
                            'failedFixedAttemptId': release_readout['lastFixedAttemptId'],
                            'validatedRange': {k: changes[k] for k in ('amp_start', 'amp_stop')}}
                    if changes != requested_changes:
                        decision['requestedParameterChanges'] = requested_changes
                        decision['normalization'] = '执行器脉冲策略及采样网格规范化'
                elif decision['action'] == 'backtrack':
                    target = self._node(current, decision['targetNodeId'])
                    decision['attemptAdmission'] = admit_backtrack(current, target)
                    if self._physical_adapter:
                        parameters, versions = self._parameters(rt, current, target)
                        trial, _ = self._readout_retry_parameters(rt, current, target, parameters, versions, {})
                        decision['budgetLookahead'] = self._validate_plan(current, target, trial, versions,
                            ident('model-backtrack-plan'), joint_budget=True, rt=rt)
            except APIError as exc:
                # Proposal validation happens after the acquisition has already
                # been recorded. It must never relabel that acquisition as an
                # execution failure or replace its quality/evidence.
                rejection = {'action': 'reject', 'reason': exc.message, 'errorCode': exc.code,
                             'evidenceIds': [], 'rejectedProposal': self._chat_summary_value(response.get('decision'))}
                self._record_decision(rt, current, node_id, rejection, 'controller')
                current['status'], current['engineState'] = 'attention', 'waiting_model'
                current_node.update(status='attention', engineState='waiting_model',
                                    detail='模型决策未执行：' + exc.message,
                                    lastDecisionError={'code': exc.code, 'message': exc.message, 'at': now()})
                self.store.save(rt, current, 'model_decision_rejected', {
                    'nodeId': node_id, 'attemptId': current_node.get('latestAttemptId'),
                    'errorCode': exc.code, 'message': current_node['detail']})
                return False
            self._record_decision(rt, current, node_id, decision, 'model')
            action = decision['action']
            if action == 'retry':
                if current_node['attempt'] >= current['maxAttemptsPerNode']:
                    current['status'], current['engineState'] = 'attention', 'budget_exhausted'
                    current_node.update(status='attention', engineState='budget_exhausted',
                                        detail='当前步骤尝试次数已达上限；' + decision['reason'])
                else:
                    changes = {k: v.get('to') if isinstance(v, dict) else v for k, v in decision.get('parameterChanges', {}).items()}
                    if release_readout:
                        release_readout.update(state='released', endedAt=now(), reason=decision['reason'],
                            rescanRequest=deepcopy(decision['readoutRevalidation']))
                        current_node.get('parameterOverrides', {}).pop('amplitudes', None)
                        current_node['parameters'].pop('amplitudes', None)
                    current_node.setdefault('parameterOverrides', {}).update(changes)
                    current_node['parameters'].update(changes)
                    current_node['engineState'], current_node['status'] = 'retry_queued', 'pending'
                    current_node['detail'] = decision['reason']
                    current['status'], current['engineState'] = 'queued', 'queued'
            elif action == 'backtrack':
                affected = self._invalidate(current, decision['targetNodeId'], include_target=True)
                current['currentNodeId'] = decision['targetNodeId']
                current['status'], current['engineState'] = 'queued', 'backtrack_queued'
            elif action == 'stop':
                current['status'], current['engineState'] = 'failed', 'budget_or_evidence_stop'
                current_node.update(status='attention', engineState='budget_or_evidence_stop', detail=decision['reason'])
            else:
                current['status'], current['engineState'] = 'attention', 'waiting_operator'
                current_node.update(status='attention', engineState='waiting_operator', detail=decision['reason'])
            self._finish_timing(current)
            self.store.save(rt, current, 'model_decision', {'message': decision['reason'], 'action': action})
        return current['status'] == 'queued'

    @staticmethod
    def _ancestors(workflow, node):
        ancestors = set(node['dependsOn'])
        while True:
            expanded = ancestors | {d for n in workflow['nodes'] if n['id'] in ancestors for d in n['dependsOn']}
            if expanded == ancestors:
                return sorted(ancestors)
            ancestors = expanded

    @staticmethod
    def _chat_summary_value(value, depth=0):
        if depth > 7:
            return None
        if value is None or isinstance(value, bool) or number(value):
            return value
        if isinstance(value, str):
            return value[:1000]
        if isinstance(value, list):
            return [Controller._chat_summary_value(v, depth + 1) for v in value[:32]]
        if isinstance(value, dict):
            denied = ('secret', 'token', 'password', 'credential', 'authorization', 'cookie', 'apikey', 'privatekey')
            return {k: Controller._chat_summary_value(v, depth + 1) for k, v in list(value.items())[:64]
                    if isinstance(k, str) and len(k) <= 128 and not any(word in k.lower().replace('_', '').replace('-', '') for word in denied)
                    and k not in ('__proto__', 'prototype', 'constructor', 'fileContents', 'rawData', 'rawText')}
        return None

    def _chat_observations(self, workflow, budget, db=None):
        """Bounded registered evidence metadata; never open an acquisition file."""
        if not workflow:
            return [], 0
        provenance_keys = {'freshAcquisition', 'operationId', 'parameterVersions', 'runnerSha256',
            'sourceManifest', 'profileHash', 'nativeHalfPolicy', 'shutdownCommandsCompleted',
            'shutdownReadbackVerified', 'adapterId', 'adapterVersion', 'methodId', 'sourceRunId',
            'ruleVersion', 'codeVersion', 'recordedAt'}
        parameter_keys = set(self._bounds()) | {'frequencies_hz', 'amplitudes', 'powers', 'start_hz',
            'stop_hz', 'span_hz', 'rb_lengths', 'rb_sequences', 'rb_master_seed',
            'rb_bootstrap_replicates', 'order_seed'}

        observations, seen, omitted, used = [], set(), 0, 0
        nodes = sorted(workflow['nodes'], key=lambda n: n['id'] != workflow.get('currentNodeId'))
        with (read_chat_snapshot(self.store.path) if db is None else nullcontext(db)) as connection:
            for node in nodes:
                latest = node.get('result') or {}
                for stage, result in (('result', latest), ('selection', latest.get('selectionResult') or {})):
                    for artifact in result.get('artifacts', []):
                        artifact_id = artifact.get('id')
                        if not isinstance(artifact_id, str) or artifact_id in seen:
                            continue
                        seen.add(artifact_id)
                        registered = connection.execute('SELECT sha256,payload FROM platform_artifacts WHERE id=?', (artifact_id,)).fetchone()
                        if not registered or registered['sha256'] != artifact.get('sha256'):
                            omitted += 1
                            continue
                        metadata = json.loads(registered['payload'])
                        summary = {'id': artifact_id, 'workflowId': workflow['id'], 'workflowMode': workflow['mode'],
                            'nodeId': node['id'], 'procedureId': node['procedureId'], 'resourceId': node['resourceId'],
                            'attemptId': node.get('latestAttemptId'), 'evidenceStage': stage,
                            'artifact': {k: metadata[k] for k in ('id', 'name', 'kind', 'sha256', 'url', 'size', 'mimeType') if k in metadata},
                            'dataKind': result.get('dataKind'), 'metrics': result.get('metrics', {}),
                            'quality': {k: result.get('quality', {}).get(k) for k in ('status', 'reasons', 'ruleVersion', 'criteria')},
                            'acquiredAt': result.get('acquiredAt'),
                            'parameters': {k: v for k, v in result.get('parameters', {}).items() if k in parameter_keys},
                            'provenance': {k: v for k, v in result.get('provenance', {}).items() if k in provenance_keys},
                            'summaryOnly': True, 'rawFileRead': False}
                        summary = self._chat_summary_value(summary)
                        size = len(dumps(summary))
                        if size > 12000 or used + size > budget or len(observations) >= 128:
                            omitted += 1
                            continue
                        observations.append(summary)
                        used += size
        return observations, omitted

    def chat(self, payload):
        message = payload.get('message')
        if not isinstance(message, str) or not message.strip() or len(message) > 20000:
            raise APIError('INVALID_MESSAGE', '消息不能为空且不能超过 20000 字符')
        selected_node_id = payload.get('nodeId')
        if selected_node_id is not None and (not isinstance(selected_node_id, str) or not selected_node_id or not payload.get('workflowId')):
            raise APIError('INVALID_CHAT_NODE', '所选节点必须属于指定实验任务')
        # Pin workflow, operations and artifact registry to one read-only snapshot.
        # Close it before waiting on the model so acquisition writes remain free.
        with read_chat_snapshot(self.store.path) as db:
            wf = self.get(payload['workflowId'], types.SimpleNamespace(db=db)) if payload.get('workflowId') else None
            selected_node = self._node(wf, selected_node_id) if selected_node_id is not None else None
            brief = None
            if wf:
                brief = {k: wf[k] for k in ('id', 'name', 'mode', 'status', 'engineState', 'revision', 'resourceIds', 'workflowComplete')}
                brief['currentNodeId'] = wf.get('currentNodeId')
                brief['nodes'] = [{k: n.get(k) for k in ('id', 'label', 'procedureId', 'engineState', 'attempt', 'latestAttemptId', 'parameters', 'parameterVersions', 'dependsOn')}
                                  | {'result': {k: n.get('result', {}).get(k) for k in ('dataKind', 'methodLabel', 'metrics', 'quality', 'acquiredAt', 'provenance', 'partial', 'verificationPending', 'stale')},
                                     'evidenceIds': [a['id'] for a in n.get('result', {}).get('artifacts', [])]}
                                  for n in wf['nodes']]
                brief = self._chat_summary_value(brief)
            context = {'workflow': brief, 'catalog': self.catalog.describe(self.adapter.capabilities(), self._bounds()),
                       'notice': '聊天建议不会直接下发设备；实验动作经过Python控制器校验。inspect_observation 仅核查本任务已登记的证据摘要，不读取原始文件；以 dataKind 和 acquiredAt 区分本轮采集与历史记录。采集状态截至 acquisitionState 的快照；扫描几何仅来自已登记且核对过哈希的 plan 元数据。'}
            if wf:
                context['acquisitionState'] = acquisition_context(db, wf, self.root, now())
            if selected_node is not None:
                context['selectedNode'] = self._chat_summary_value({
                    'workflowId': wf['id'], 'workflowRevision': wf['revision'], 'readOnly': True,
                    **{k: selected_node.get(k) for k in ('id', 'label', 'procedureId', 'engineState', 'attempt',
                        'latestAttemptId', 'parameters', 'parameterVersions', 'dependsOn')},
                    'result': {k: (selected_node.get('result') or {}).get(k) for k in
                        ('dataKind', 'metrics', 'quality', 'acquiredAt', 'partial', 'verificationPending', 'stale')}})
            observation_budget = max(0, min(60000, 170000 - len(dumps(context)) - len(message)))
            context['observations'], omitted = self._chat_observations(wf, observation_budget, db)
            context['observationSummary'] = {'provided': len(context['observations']), 'omitted': omitted,
                                             'source': 'registered_workflow_evidence_metadata', 'rawFileRead': False}
            request = bound_chat_observations({'message': message, 'sessionId': payload.get('sessionId'), 'context': context})
        result = self._brain('/chat', request)
        return {'sessionId': result.get('sessionId', payload.get('sessionId', '')), 'message': result.get('message') or result.get('error') or '模型暂时不可用',
                'status': result.get('status', 'error'), 'model': result.get('model')}

    def health(self):
        brain_address = urllib.parse.urlsplit(self.brain_url)
        agent_port = brain_address.port or (443 if brain_address.scheme == 'https' else 80)
        return {'status': 'ok', 'version': '0.2.0', 'projectId': 'qcal-local-f8-platform', 'projectRoot': str(self.root.resolve()), 'agentPort': agent_port, 'agent': self._brain('/health', timeout=3),
                'hardware': {**self.adapter.preflight(), 'ownership': self.hardware_ownership()}, 'capabilities': self.adapter.capabilities()}

    def hardware_ownership(self, refresh=False):
        if not self._physical_adapter:
            return {'verified': True, 'owners': [], 'reason': 'Explicit test adapter; no physical endpoint'}
        if refresh or self._ownership_cache is None or time.time() - self._ownership_cache.get('checkedAt', 0) > 15:
            value = self.adapter.hardware_ownership(refresh=refresh)
            if not isinstance(value, dict) or value.get('verified') is not True or not isinstance(value.get('owners'), list):
                value = {'verified': False, 'owners': [], 'reason': '物理执行器独占状态无法确认'}
            self._ownership_cache = value
        return self._ownership_cache

    def account(self, action, payload=None):
        from .account import account_request
        # Account mutations and model tests are only exposed by explicit UI
        # actions. Model conversations cannot send SMS or log accounts in/out.
        # Includes official login/profile/catalog calls and encrypted receipt writes.
        timeout = 125 if action == 'test' else 90 if payload is not None else 25
        return account_request(self.brain_url, action, payload, timeout=timeout)

    def report(self, workflow_id):
        # One read-only WAL snapshot: a concurrent measurement cannot mix old
        # workflow quality with new attempts or events in the same report.
        with read_chat_snapshot(self.store.path) as db:
            wf = self.get(workflow_id, types.SimpleNamespace(db=db))
            nodes = []
            for node in wf['nodes']:
                attempts = [json.loads(row[0]) for row in db.execute(
                    'SELECT payload FROM platform_attempts WHERE workflow_id=? AND node_id=? ORDER BY number', (workflow_id, node['id']))]
                decisions = [json.loads(row[0]) for row in db.execute(
                    'SELECT payload FROM platform_decisions WHERE workflow_id=? AND node_id=? ORDER BY rowid', (workflow_id, node['id']))]
                nodes.append({'node': node, 'attempts': attempts, 'decisions': decisions})
            events = [{**dict(row), 'payload': json.loads(row['payload'])} for row in db.execute(
                'SELECT * FROM events WHERE run_id=? ORDER BY seq', (workflow_id,))]
            return {'schema': 'qcal-report/1', 'generatedAt': now(), 'workflow': wf, 'nodes': nodes,
                    'events': events, 'source': 'recorded_data_only', 'workflowComplete': wf['workflowComplete']}

    def archive_status(self):
        return self.archive.status()

    def archive_list(self, workflow_id=None, limit=30, cursor=None):
        if workflow_id:
            self.get(workflow_id)
        return self.archive.list(workflow_id, limit, cursor)

    def archive_item(self, archive_id):
        return self.archive.item(archive_id)

    def knowledge(self):
        with self.store.connect() as rt:
            return {'rules': self.catalog.rules(), 'candidates': self.experience.list(rt),
                    'published': self.experience.published_rules(rt),
                    'semantics': '已发布经验仅供新任务引用；当前任务的门槛和规则快照保持冻结'}

    def publish_experience(self, candidate_id, operation_id):
        with self.store.transaction() as rt:
            rule = self.experience.publish(rt, candidate_id, operation_id)
            rt._event(None, 'experience_published', {'ruleId': rule['id'], 'candidateId': candidate_id})
            return rule
