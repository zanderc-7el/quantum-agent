"""Optional current-attempt diagnostics, authenticated before offline calculation.

No hardware, calibration, quality, database or stored-result mutation. A missing
or inconsistent source is unavailable, never a reason to accept or reject a run.
Only the audited ArcLight complete T1/Ramsey/Echo/CPMG schemas are supported.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess

from .chat_acquisition_context import read_chat_snapshot
from .common import APIError, checked_path, file_hash
from .prior_failure_context import (CHECKED_JSON_NAMES, load_audited_bindings,
    _validate_effect, javascript_json_units_upper_bound)

VERSION = 'qcal-capture-order-diagnostics/2'
PROCEDURES = {'sq_t1': 't1', 'sq_ramsey': 'ramsey', 'sq_echo': 'echo', 'sq_cpmg': 'cpmg'}
MAX_JSON_BYTES = 2 * 1024**2
MAX_RAW_BYTES = 256 * 1024**2
MAX_CONTEXT_BYTES = 16384
MAX_INPUT_UNITS = 198000
MAX_CAPTURES = 32
TIMING_PROJECTION_FIELDS = frozenset(('elapsed', 'firstAcquisitionAt', 'finishedAt'))
NOTICE = ('描述统计，非验收或因果结论。每个组/capture 单独使用原生保存的投影与拟合，未重拟合、平滑或扣除漂移。'
          '相关系数的横坐标是 raw/axes 中的原始采集行序号，不是扫描坐标，也不是已核验的实际时间。'
          '采集顺序关联仅是漂移候选线索；随机扫描坐标也可能与采集顺序偶然相关。'
          'SEM 和残差比不证明独立性或唯一噪声来源，不提供通过保证。未登记时间文件未读取，无法报告每次采集时长。'
          '当前原生分析、平台质量标准、证据白名单和参数版本全部保持原样。')
NOTICE += ('完整采集不等于每个capture均有有效拟合。partial_fit_diagnostics表示完整原始数据已核验，但至少一个原生fit为null；'
           '原metrics.r_squared仅汇总非null拟合，不能代表所有capture通过。无拟合capture的原始信号与采集顺序相关也可能来自扫描信号本身，不能当作残差或漂移证明。')


def unavailable(code):
    return {'schema': VERSION, 'status': 'unavailable', 'reasonCode': code,
            'scope': 'current_attempt_diagnostic_only_not_acceptance', 'notice': NOTICE}


def _require(condition, code):
    if not condition:
        raise ValueError(code)


def _document(path):
    with path.open('rb') as stream:
        data = stream.read(MAX_JSON_BYTES + 1)
    _require(0 < len(data) <= MAX_JSON_BYTES, 'json_size_limit')
    return json.loads(data.decode('utf-8-sig'))


def _registered_files(db, root, operation, result):
    op_root = checked_path(root / 'data/runs/arclight_operations' / hashlib.sha256(operation.encode()).hexdigest(), parent=root)
    inventory = result.get('artifacts')
    _require(isinstance(inventory, list) and all(isinstance(a, dict) for a in inventory), 'artifact_inventory_mismatch')
    needed = [a for a in inventory if a.get('kind') == 'raw' or a.get('name') in CHECKED_JSON_NAMES | {'axes.json'}]
    _require(1 <= len(needed) <= 16, 'artifact_inventory_limit')
    files = []
    for artifact in needed:
        registered = db.execute('SELECT * FROM platform_artifacts WHERE id=?', (artifact['id'],)).fetchone()
        _require(registered is not None and registered['sha256'] == artifact['sha256']
                 and artifact['id'] == 'real-' + artifact['sha256'][:24], 'artifact_not_registered')
        payload = json.loads(registered['payload'])
        _require(payload.get('kind') == artifact['kind'], 'artifact_kind_mismatch')
        # Registry IDs are content addressed: a later identical source JSON may
        # update its canonical registry path. Validate this operation's exact
        # sourcePath against that registered content hash, never a sibling job.
        path = checked_path(artifact['sourcePath'], parent=op_root)
        _require(path.name == artifact['name'], 'artifact_name_mismatch')
        limit = MAX_RAW_BYTES if artifact['name'] == 'raw_scan.npz' else MAX_JSON_BYTES
        _require(0 < path.stat().st_size <= limit, 'artifact_size_limit')
        _require(file_hash(path) == artifact['sha256'], 'artifact_hash_mismatch')
        entry = {'artifactRef': artifact['id'], 'sha256': artifact['sha256'], 'name': artifact['name'],
                 'kind': artifact['kind'], 'folder': path.parent.relative_to(op_root).as_posix(),
                 'path': str(path), 'registryHashVerified': True}
        if path.name != 'raw_scan.npz':
            entry['document'] = _document(path)
        files.append(entry)
    _require(len({f['name'] for f in files}) == len(files), 'single_job_inventory_required')
    _require({'raw_scan.npz', 'axes.json', 'job.json', 'status.json', 'platform_analysis.json', 'plan.json'}
             <= {f['name'] for f in files}, 'required_artifact_missing')
    _require(next(f['folder'] for f in files if f['name'] == 'axes.json') ==
             next(f['folder'] for f in files if f['name'] == 'raw_scan.npz'), 'axes_raw_job_folder_mismatch')
    return files


def resolve_sources(root, db_path, workflow, node, *, implementation_id):
    """Pin one read-only SQLite snapshot; never fall back to a prior attempt.

    The caller must provide the real adapter class identity. The audited profile
    bytes, frozen fingerprint and actual source SHA are independently checked.
    The returned paths are internal worker input, never sent to the model.
    """
    if node.get('procedureId') not in PROCEDURES:
        return unavailable('unsupported_procedure')
    try:
        root = checked_path(root)
        bindings = load_audited_bindings(root, implementation_id)
        binding = bindings.get(workflow.get('adapterId'))
        _require(binding is not None and workflow.get('adapterProfileHash') == binding['fingerprint']
                 and workflow.get('mode') == 'real' and node.get('resourceId') == 'Q11', 'unaudited_binding')
        source_hashes = {}
        for name in ('platform_analysis.py', 'analyze_single_qubit.py'):
            path = checked_path(root / (binding['analysisSourcePath'] if name == 'platform_analysis.py'
                                       else 'adapters/arclight_runtime/' + name), parent=root)
            source_hashes[name] = binding['manifest'][name]
            _require(file_hash(path) == source_hashes[name], 'analysis_source_changed')
        with read_chat_snapshot(db_path) as db:
            row = db.execute('SELECT payload FROM platform_workflows WHERE id=?', (workflow['id'],)).fetchone()
            _require(row is not None, 'workflow_missing')
            stored = json.loads(row[0])
            # Controller.get enriches these three display-time fields in memory
            # via _with_timing. They are not acquisition evidence. Compare every
            # other persistent field exactly, including revision, nodes/results,
            # adapter binding, gates and parameter versions. Native completion
            # and acquisition identity still come from the effect/attempt below.
            persisted_view = lambda value: {k: v for k, v in value.items() if k not in TIMING_PROJECTION_FIELDS}
            _require(persisted_view(stored) == persisted_view(workflow), 'stale_workflow_snapshot')
            nodes = [n for n in stored['nodes'] if n.get('id') == node.get('id')]
            _require(len(nodes) == 1 and nodes[0] == node and node.get('latestAttemptId'), 'current_node_mismatch')
            row = db.execute('SELECT payload FROM platform_attempts WHERE id=? AND workflow_id=? AND node_id=?',
                (node['latestAttemptId'], workflow['id'], node['id'])).fetchone()
            _require(row is not None, 'current_attempt_missing')
            attempt = json.loads(row[0])
            _require(attempt.get('state') in ('completed', 'attention') and attempt.get('endedAt')
                     and not attempt.get('error') and attempt.get('mode') == 'real'
                     and attempt.get('procedureId') == node['procedureId']
                     and attempt.get('result') == node.get('result'), 'incomplete_or_mismatched_attempt')
            result = attempt['result']
            _require(not any(result.get(k) for k in ('partial', 'stale', 'verificationPending')), 'partial_or_stale_result')
            operation = attempt['operationId']
            _require(operation == attempt['id'] + '/measure', 'operation_identity_mismatch')
            operations = [operation]
            if attempt.get('verificationOperationId'):
                _require(attempt['verificationOperationId'] == attempt['id'] + '/verify', 'verification_identity_mismatch')
                operations.append(attempt['verificationOperationId'])
            expected = [result['selectionResult'], result] if len(operations) == 2 else [result]
            _require(len(operations) == 2 or not result.get('selectionResult'), 'result_role_mismatch')
            effects = list(db.execute('SELECT * FROM platform_effects WHERE attempt_id=?', (attempt['id'],)))
            _require(len(effects) == len(operations), 'unexpected_attempt_effect')
            sources, raw_sets = [], []
            for role, operation, original in zip(['selection', 'independent_verification'], operations, expected):
                matches = [dict(e) for e in effects if e['operation_id'] == operation]
                _require(len(matches) == 1 and matches[0]['state'] == 'completed', 'incomplete_effect')
                effect = matches[0]
                effect['request'], effect['result'] = json.loads(effect['request']), json.loads(effect['result'])
                files = _registered_files(db, root, operation, effect['result'])
                prior_files = [f for f in files if f['kind'] == 'raw' or f['name'] in CHECKED_JSON_NAMES]
                raw_sets.append(_validate_effect(effect, prior_files, workflow, attempt, binding, operation, original))
                plan = next(f['document'] for f in files if f['name'] == 'plan.json')
                _require(len(plan['jobs']) == 1 and plan['jobs'][0]['kind'] == PROCEDURES[node['procedureId']], 'unsupported_job_schema')
                sources.append({'resultRole': role, 'operationId': operation,
                    'files': [{k: f[k] for k in ('name', 'path', 'sha256', 'folder')} for f in files]})
            _require(len(raw_sets) == 1 or not raw_sets[0].intersection(raw_sets[1]), 'raw_reused_across_operations')
        return {'status': 'resolved', 'schema': VERSION, 'root': str(root), 'python': binding['profile']['python'],
            'binding': {'runId': workflow['id'], 'nodeId': node['id'], 'revision': workflow['revision'],
                        'sourceAttempt': attempt['id'], 'procedureId': node['procedureId']},
            'sourceBinding': {'adapterId': workflow['adapterId'], 'adapterProfileHash': binding['fingerprint'],
                              'sourceManifestSha256': binding['sourceManifestSha256']},
            'sourceHashes': source_hashes, 'operations': sources}
    except (APIError, OSError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError, IndexError, RuntimeError, StopIteration) as exc:
        # Fixed codes only; never leak file paths, arbitrary raw JSON or errors.
        code = str(exc) if type(exc) is ValueError and str(exc).replace('_', '').isalnum() and len(str(exc)) < 80 else 'source_validation_failed'
        return unavailable(code)


def calculate_diagnostics(resolved):
    """Run the scientific reader in the audited local NumPy runtime, offline."""
    if resolved.get('status') != 'resolved':
        return deepcopy(resolved)
    try:
        # No shell, stdin is bounded metadata only, no instrument-capable entry.
        proc = subprocess.run([resolved['python'], '-X', 'utf8', '-m', 'backend.capture_diagnostics_worker'],
            input=json.dumps(resolved, allow_nan=False), capture_output=True, encoding='utf-8',
            cwd=Path(__file__).resolve().parents[1], timeout=45, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        _require(proc.returncode == 0 and len(proc.stdout.encode('utf-8')) <= MAX_CONTEXT_BYTES, 'diagnostic_worker_failed')
        output = json.loads(proc.stdout)
        _require(output.get('schema') == VERSION and output.get('status') in ('available', 'partial_fit_diagnostics', 'unavailable'), 'diagnostic_worker_schema')
        if output['status'] in ('available', 'partial_fit_diagnostics'):
            _require(output.get('binding') == resolved['binding'], 'diagnostic_worker_binding')
        return output
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError):
        return unavailable('diagnostic_worker_unavailable')


def attach_diagnostics(payload, diagnostics):
    """Never squeeze out current evidence or relax the unchanged agent limit."""
    output = deepcopy(payload)
    if diagnostics.get('status') in ('available', 'partial_fit_diagnostics') and any(diagnostics.get('binding', {}).get(k) != payload.get(k)
            for k in ('runId', 'nodeId', 'revision')):
        diagnostics = unavailable('stale_diagnostic_binding')
    if len(json.dumps(diagnostics, ensure_ascii=False, allow_nan=False).encode('utf-8')) > MAX_CONTEXT_BYTES:
        diagnostics = unavailable('diagnostic_size_limit')
    output['captureDiagnostics'] = deepcopy(diagnostics)
    if javascript_json_units_upper_bound(output) <= MAX_INPUT_UNITS:
        return output
    output['captureDiagnostics'] = {'schema': VERSION, 'status': 'unavailable', 'reasonCode': 'optional_context_budget'}
    return output if javascript_json_units_upper_bound(output) <= MAX_INPUT_UNITS else deepcopy(payload)
