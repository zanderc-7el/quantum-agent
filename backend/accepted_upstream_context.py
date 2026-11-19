"""Read-only, authenticated current upstream coherence references.

No calibration reuse, fitting, gate changes, evidence allowlist changes or writes.
First version deliberately covers only the audited T1/Ramsey/Echo receipt schema.
"""
from copy import deepcopy
import json
import sqlite3

from .capture_diagnostics import _registered_files, TIMING_PROJECTION_FIELDS
from .chat_acquisition_context import read_chat_snapshot
from .common import APIError, checked_path, digest, file_hash, number
from .prior_failure_context import (PROFILE_FILES, CHECKED_JSON_NAMES, audited_bindings,
    _validate_effect, javascript_json_units_upper_bound)
from .quality import evaluate

VERSION = 'qcal-current-accepted-upstream/1'
SUPPORTED = frozenset(('sq_t1', 'sq_ramsey', 'sq_echo'))
MAX_CASES, MAX_BYTES, MAX_INPUT_UNITS = 3, 16384, 198000
NOTICE = ('当前同任务、同物理参数版本的已独立验收上游参考，仅供下一步判断；不是当前失败节点的证据，'
          '其 sourceReferences 不可用作当前 decision.evidenceIds，不复用校准、不改变验收或解除回退预算。'
          '首测与独立复测 metrics 分开保留；两者差异仍需评估，已通过不表示高稳定性。'
          '仅支持 T1/Ramsey/Echo；其他方法未投影。未重拟合、未补缺失指标。')


def _require(value, code):
    if not value:
        raise ValueError(code)


def _base(workflow, node):
    return {'schema': VERSION, 'status': 'available', 'scope': 'current_verified_upstream_reference_only',
            'supportedProcedures': sorted(SUPPORTED), 'notice': NOTICE,
            'binding': {'runId': workflow.get('id'), 'nodeId': node.get('id'), 'revision': workflow.get('revision')},
            'limits': {'maximumCases': MAX_CASES, 'maximumBytes': MAX_BYTES}, 'cases': [], 'excluded': []}


def _ancestors(workflow, node):
    nodes = {n['id']: n for n in workflow['nodes']}
    _require(len(nodes) == len(workflow['nodes']) <= 64 and node['id'] in nodes, 'graph_mismatch')
    found, pending = set(), list(node['dependsOn'])
    while pending:
        value = pending.pop()
        _require(value in nodes and value != node['id'], 'graph_mismatch')
        if value not in found:
            found.add(value)
            pending.extend(nodes[value]['dependsOn'])
    # Nearest eligible ancestors first, then earlier validated coherence steps.
    return sorted((nodes[n] for n in found), key=lambda n: (n['id'] not in node['dependsOn'], n['id']))


def _current_physical(db, workflow, node, binding):
    values = {k: v for k, v in binding['profile']['resources'][node['resourceId']].items()
              if k not in ('q', 'calibrationStatus')}
    versions = {key: 0 for key in values}
    prefix = workflow['id'] + ':' + node['resourceId'] + ':'
    for key in values:
        row = db.execute('SELECT value,revision FROM parameters WHERE name=?', (prefix + key,)).fetchone()
        if row:
            values[key], versions[key] = json.loads(row['value']), row['revision']
        _require(type(versions[key]) is int and versions[key] >= 0, 'invalid_parameter_ledger')
        if key in node.get('parameterOverrides', {}):
            values[key] = node['parameterOverrides'][key]
    return values, versions


def _case(db, root, workflow, upstream, current_values, current_versions, binding):
    _require(upstream.get('engineState') == 'verified' and upstream.get('status') == 'success'
             and upstream.get('mainProcedureComplete') is True, 'upstream_not_current_verified')
    row = db.execute('SELECT payload FROM platform_attempts WHERE workflow_id=? AND node_id=? '
                     'ORDER BY number DESC LIMIT 1', (workflow['id'], upstream['id'])).fetchone()
    _require(row is not None, 'upstream_attempt_missing')
    attempt = json.loads(row[0])
    _require(attempt.get('id') == upstream.get('latestAttemptId') and attempt.get('number') == upstream.get('attempt')
             and attempt.get('state') == 'verified' and attempt.get('mode') == 'real'
             and attempt.get('endedAt') and not attempt.get('error')
             and attempt.get('procedureId') == upstream['procedureId']
             and attempt.get('result') == upstream.get('result'), 'upstream_attempt_mismatch')
    result = attempt['result']
    _require(not any(result.get(k) for k in ('partial', 'stale', 'verificationPending')),
             'upstream_partial_or_stale')
    _require(result.get('quality', {}).get('status') == 'passed'
             and result['quality'].get('criteria') == workflow['gates'][upstream['procedureId']]
             and result['quality'].get('ruleVersion') == workflow['gatesVersion']
             and result['quality'].get('reasons') == [], 'upstream_not_accepted')
    for key, value in current_values.items():
        _require(result.get('parameters', {}).get(key) == value
                 and attempt.get('parameterVersions', {}).get(key) == current_versions[key]
                 and upstream.get('parameterVersions', {}).get(key) == current_versions[key],
                 'upstream_physical_context_changed')
    operations = [attempt.get('operationId'), attempt.get('verificationOperationId')]
    _require(operations == [attempt['id'] + '/measure', attempt['id'] + '/verify'], 'independent_operation_missing')
    effects = list(db.execute('SELECT * FROM platform_effects WHERE attempt_id=?', (attempt['id'],)))
    _require(len(effects) == 2, 'unexpected_attempt_effect')
    selection = result.get('selectionResult')
    _require(isinstance(selection, dict) and selection.get('quality', {}).get('status') == 'unverified',
             'selection_role_mismatch')
    summaries, raw_sets = [], []
    for role, operation, expected in zip(('selection', 'independent_verification'), operations, (selection, result)):
        _require(not any(expected.get(k) for k in ('partial', 'stale', 'verificationPending')),
                 'upstream_partial_or_stale')
        _require(expected.get('analysisQuality', {}).get('accepted') is True
                 and evaluate(expected, upstream['procedureId'], 'real', workflow['gates'], independent=True)
                     ['quality']['status'] == 'passed', 'upstream_native_or_numeric_gate_failed')
        _require(all(expected.get('parameters', {}).get(k) == v for k, v in current_values.items()),
                 'upstream_physical_context_changed')
        matches = [dict(e) for e in effects if e['operation_id'] == operation]
        _require(len(matches) == 1 and matches[0]['state'] == 'completed', 'upstream_incomplete_effect')
        effect = matches[0]
        effect['request'], effect['result'] = json.loads(effect['request']), json.loads(effect['result'])
        files = _registered_files(db, root, operation, effect['result'])
        checked = [f for f in files if f['kind'] == 'raw' or f['name'] in CHECKED_JSON_NAMES]
        raw_sets.append(_validate_effect(effect, checked, workflow, attempt, binding, operation, expected))
        status = next(f['document'] for f in files if f['name'] == 'status.json')
        summaries.append({'role': role, 'operationId': operation, 'acquiredAt': expected['acquiredAt'],
            'completedAt': status.get('ended'), 'completedAtBasis': 'registered_native_status_local_time_no_timezone_conversion',
            'metrics': {k: v for k, v in expected.get('metrics', {}).items() if number(v)},
            'formalQuality': deepcopy(expected['quality']), 'nativeAccepted': True,
            'sourceReferences': [{k: f[k] for k in ('name', 'sha256', 'artifactRef')}
                                 for f in files if f['name'] in ('raw_scan.npz', 'platform_analysis.json', 'status.json', 'plan.json')]})
    _require(raw_sets[0] and raw_sets[1] and raw_sets[0].isdisjoint(raw_sets[1]), 'upstream_raw_reused')
    return {'nodeId': upstream['id'], 'procedureId': upstream['procedureId'], 'resourceId': upstream['resourceId'],
            'sourceAttempt': attempt['id'], 'attemptReceiptSha256': digest(attempt), 'acceptedAt': attempt['endedAt'],
            'physicalParameters': deepcopy(current_values), 'physicalParameterVersions': deepcopy(current_versions),
            'status': 'current_independently_verified', 'operations': summaries}


def resolve_accepted_upstream(root, db_path, workflow, node, *, implementation_id):
    output = _base(workflow, node)
    try:
        root = checked_path(root)
        bindings = audited_bindings(implementation_id,
            {identity: checked_path(root / name, parent=root).read_bytes() for identity, (name, _) in PROFILE_FILES.items()},
            checked_path(root / 'adapters/arclight_runtime/source_manifest.json', parent=root).read_bytes())
        binding = bindings.get(workflow.get('adapterId'))
        _require(binding is not None and workflow.get('adapterProfileHash') == binding['fingerprint']
                 and workflow.get('mode') == 'real' and node.get('resourceId') == 'Q11', 'unaudited_binding')
        for name in ('platform_analysis.py', 'analyze_single_qubit.py'):
            _require(file_hash(checked_path(root / 'adapters/arclight_runtime' / name, parent=root))
                     == binding['manifest'][name], 'analysis_source_changed')
        with read_chat_snapshot(db_path) as db:
            row = db.execute('SELECT payload FROM platform_workflows WHERE id=?', (workflow['id'],)).fetchone()
            _require(row is not None, 'workflow_missing')
            stored = json.loads(row[0])
            view = lambda value: {k: v for k, v in value.items() if k not in TIMING_PROJECTION_FIELDS}
            _require(view(stored) == view(workflow), 'stale_workflow_snapshot')
            _require([n for n in stored['nodes'] if n['id'] == node['id']] == [node], 'current_node_mismatch')
            values, versions = _current_physical(db, stored, node, binding)
            for upstream in _ancestors(stored, node):
                if upstream.get('resourceId') != node['resourceId'] or upstream.get('procedureId') not in SUPPORTED:
                    continue
                try:
                    case = _case(db, root, stored, upstream, values, versions, binding)
                    candidate = {**output, 'cases': output['cases'] + [case]}
                    _require(len(candidate['cases']) <= MAX_CASES
                             and len(json.dumps(candidate, ensure_ascii=False, allow_nan=False).encode('utf8')) <= MAX_BYTES,
                             'optional_context_budget')
                    output = candidate
                except (APIError, OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, StopIteration) as exc:
                    output['excluded'].append({'nodeId': upstream['id'], 'reasonCode': _code(exc)})
        return output
    except (APIError, OSError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError, IndexError, RuntimeError) as exc:
        return {**_base(workflow, node), 'status': 'unavailable', 'reasonCode': _code(exc)}


def _code(exc):
    return str(exc) if type(exc) is ValueError and str(exc).replace('_', '').isalnum() and len(str(exc)) < 80 else 'source_validation_failed'


def attach_accepted_upstream(payload, context):
    output = deepcopy(payload)
    if any(context.get('binding', {}).get(k) != payload.get(k) for k in ('runId', 'nodeId', 'revision')):
        context = {'schema': VERSION, 'status': 'unavailable', 'reasonCode': 'stale_context_binding'}
    if len(json.dumps(context, ensure_ascii=False, allow_nan=False).encode('utf8')) > MAX_BYTES:
        context = {'schema': VERSION, 'status': 'unavailable', 'reasonCode': 'optional_context_budget'}
    output['acceptedUpstreamContext'] = deepcopy(context)
    if javascript_json_units_upper_bound(output) <= MAX_INPUT_UNITS:
        return output
    output['acceptedUpstreamContext'] = {'schema': VERSION, 'status': 'unavailable', 'reasonCode': 'optional_context_budget'}
    return output if javascript_json_units_upper_bound(output) <= MAX_INPUT_UNITS else deepcopy(payload)
