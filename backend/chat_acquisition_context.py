"""Read-only, bounded acquisition status for chat; never an acceptance gate.

Only registered plan JSON is opened. Raw acquisition files are never read.
All database rows must come from the caller's one pinned SQLite read snapshot.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import sqlite3

from .common import APIError, checked_path, number
from .prior_failure_context import javascript_json_units_upper_bound

MAX_PLAN_BYTES = 1024 * 1024
MAX_PLAN_READ_BYTES = 4 * MAX_PLAN_BYTES
MAX_CHAT_UNITS = 190000  # Below the agent's unchanged 200,000 UTF-16-unit limit.
IDENTITY = ('runId', 'nodeId', 'attemptId', 'operationId', 'resourceId', 'procedureId', 'parameterVersions')
METRICS = ('contrast_snr', 'peak_snr', 'resolved_power_points', 'scan_coverage', 'r_squared', 'cycles',
           'assignment_fidelity', 'relative_stderr', 't1_s', 'gate_fidelity', 'reference_drift_fraction')
MAX_RAW_METADATA = 16384
PARAMETER_FACTS = ('readout_hz', 'drive_hz', 'readout_amp', 'readout_width_s', 'drive_amp',
    'drive_length_s', 'half_pi_amp', 'shape_pi_amp', 'pulse_length_s', 'f12_hz',
    'probe12_amplitude', 'drag_lambda1', 'drag_beta_s')


def beijing_time(value):
    """Convert an offset-bearing source time; never assign a timezone to naive data."""
    if not isinstance(value, str) or len(value) > 80:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp.astimezone(timezone(timedelta(hours=8))).isoformat() if stamp.tzinfo else None
    except ValueError:
        return None


def parameter_facts(values, role):
    facts = {}
    if not isinstance(values, dict):
        return facts
    for key in PARAMETER_FACTS:
        value = values.get(key)
        if not number(value):
            continue
        fact = {'value': value, 'role': role}
        if key.endswith('_hz'):
            decimal = Decimal(str(value))
            fact['unit'] = 'Hz'
            fact['frequencyUnits'] = {name: format(decimal / divisor, 'f')
                for name, divisor in [('MHz', Decimal(10)**6), ('GHz', Decimal(10)**9)]}
        elif key.endswith('_s'):
            fact['unit'] = 's'
        facts[key] = fact
    return facts


def _projection(column, fields, path='$'):
    # Both arguments are internal constants, never client strings. JSON extraction
    # keeps large arrays/artifact lists out of the Python/model context.
    source = f"CASE WHEN json_valid({column}) THEN {column} ELSE '{{}}' END"
    def value(key):
        # -> keeps original JSON tokens: json_extract turns true into integer 1
        # and re-encoding a SQLite REAL can discard source decimal digits.
        return f"json(({source}) -> '{path}.{key}')"
    return 'json_object(' + ','.join(f"'{key}',{value(key)}" for key in fields) + ')'


def _registered_operation_raw(db, operation):
    rows = db.execute("""SELECT json_extract(a.value,'$.sha256') AS sha,
        r.sha256 AS registered_sha,json_extract(CASE WHEN json_valid(r.payload) THEN r.payload ELSE '{}' END,'$.sha256') AS metadata_sha,
        json_extract(a.value,'$.id') AS id,json_extract(CASE WHEN json_valid(r.payload) THEN r.payload ELSE '{}' END,'$.id') AS registered_id,
        json_extract(CASE WHEN json_valid(r.payload) THEN r.payload ELSE '{}' END,'$.kind') AS registered_kind
        FROM platform_effects e JOIN json_each(CASE WHEN json_valid(e.result) THEN e.result ELSE '{}' END,'$.artifacts') a
        LEFT JOIN platform_artifacts r ON r.id=json_extract(a.value,'$.id')
        WHERE e.operation_id=? AND json_valid(e.result) AND json_valid(a.value)
        AND json_extract(a.value,'$.kind')='raw' LIMIT ?""", (operation, MAX_RAW_METADATA + 1)).fetchall()
    if len(rows) > MAX_RAW_METADATA:
        return set()
    hashes = set()
    for row in rows:
        sha = row['sha']
        if (not isinstance(sha, str) or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha)
                or sha != row['registered_sha'] or sha != row['metadata_sha']
                or row['id'] != row['registered_id'] or row['registered_kind'] != 'raw'):
            return set()
        hashes.add(sha)
    return hashes


def _execution_matches(workflow, node, attempt, request):
    binding = attempt.get('executionBinding')
    if not binding:
        return (not request.get('executionBinding') and request.get('adapterId') == workflow.get('adapterId')
                and request.get('adapterProfileHash') == workflow.get('adapterProfileHash'))
    # Read-only metadata identity, not a substitute for the native execution gate.
    versions = {'qcal-attempt-execution-binding/1': 'arclight-private-f8-q11-rabi16-v1',
                'qcal-smart-attempt-execution-binding/1': 'arclight-private-f8-q11-smart-rabi16-v1'}
    return (isinstance(binding, dict) and binding.get('schema') in versions
        and node['procedureId'] == 'sq_rabi_amplitude' and node['resourceId'] == 'Q11'
        and request.get('executionBinding') == binding
        and binding.get('stage') == 'acquisition' and versions.get(binding.get('schema')) == request.get('adapterId')
        and all(binding.get(k) == v for k, v in {
            'baseAdapterId': workflow.get('adapterId'), 'baseAdapterProfileHash': workflow.get('adapterProfileHash'),
            'workflowId': workflow['id'], 'nodeId': node['id'], 'attemptId': attempt['id'],
            'resourceId': node['resourceId'], 'procedureId': node['procedureId'], 'attemptNumber': attempt.get('number'),
            'adapterId': request.get('adapterId'), 'adapterProfileHash': request.get('adapterProfileHash'),
            'selectionOperationId': attempt['id'] + '/measure', 'verificationOperationId': attempt['id'] + '/verify'}.items()))


def _committed(db, workflow_id, resource_id):
    prefix = workflow_id + ':' + resource_id + ':'
    values = {}
    for row in db.execute('SELECT name,value,revision FROM parameters WHERE substr(name,1,?)=? LIMIT 65',
                          (len(prefix), prefix)):
        key = row['name'][len(prefix):]
        if key not in PARAMETER_FACTS or len(row['value']) > 128:
            continue
        try:
            fact = parameter_facts({key: json.loads(row['value'])}, 'committed_ledger_value').get(key)
        except (ValueError, TypeError):
            fact = None
        if fact:
            values[key] = {**fact, 'revision': row['revision'],
                'meaning': 'Stored committed value; does not establish validity under the current measurement conditions.'}
    return values


@contextmanager
def read_chat_snapshot(path):
    """Avoid Runtime initialization/schema writes, including on a live database."""
    db = sqlite3.connect(checked_path(path).as_uri() + '?mode=ro', uri=True,
                         isolation_level=None, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        yield db
    finally:
        db.rollback()
        db.close()


def _object(raw):
    if not isinstance(raw, str) or len(raw) > 2 * MAX_PLAN_BYTES:
        return None
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except (ValueError, TypeError):
        return None


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _registered_raw(db, result):
    hashes = set()
    artifacts = result.get('artifacts', [])
    if not isinstance(artifacts, list) or len(artifacts) > 128:
        return hashes
    for artifact in artifacts:
        if not isinstance(artifact, dict) or artifact.get('kind') != 'raw':
            continue
        row = db.execute('SELECT sha256,payload FROM platform_artifacts WHERE id=?',
                         (artifact.get('id'),)).fetchone()
        metadata = _object(row['payload']) if row else None
        if (metadata and metadata.get('kind') == 'raw' and metadata.get('id') == artifact.get('id')
                and metadata.get('sha256') == row['sha256'] == artifact.get('sha256')
                and isinstance(row['sha256'], str) and len(row['sha256']) == 64):
            hashes.add(row['sha256'])
    return hashes


def _geometry(plan):
    jobs, rows = plan.get('jobs'), plan.get('expectedJobRows')
    if (not isinstance(jobs, list) or not 1 <= len(jobs) <= 8 or not isinstance(rows, list)
            or len(rows) != len(jobs) or any(not _integer(n, 1) for n in rows)
            or plan.get('totalRows') != sum(rows)):
        return None
    summaries, total_shots = [], 0
    for job, expected in zip(jobs, rows):
        if not isinstance(job, dict):
            return None
        spec, captures, blocks = job.get('spec', {}), job.get('captures'), job.get('acquisitions_per_point')
        meta = spec.get('meta', {})
        shots, sweeps = meta.get('shots'), spec.get('sweeps', [])
        if (not all(_integer(n, 1) for n in (captures, blocks, shots)) or not isinstance(sweeps, list)
                or len(sweeps) > 16):
            return None
        dimensions = []
        for axis in sweeps:
            values = axis.get('values') if isinstance(axis, dict) else None
            if (not isinstance(values, list) or not values or len(values) > 100000
                    or not isinstance(axis.get('name'), str) or len(axis['name']) > 128):
                return None
            dimensions.append({'name': axis['name'], 'points': len(values)})
        # Type 1 is the native Cartesian/zip scan. AllXY/RB have separate row
        # definitions; show their authenticated planned rows without inventing axes.
        if meta.get('type') == '1':
            if meta.get('expand_mode') == 'cartesian':
                calculated = math.prod(axis['points'] for axis in dimensions) * captures
            elif meta.get('expand_mode') == 'zip' and dimensions and len({a['points'] for a in dimensions}) == 1:
                calculated = dimensions[0]['points'] * captures
            else:
                return None
            if calculated != expected:
                return None
        per_point = shots * blocks
        summary = {'kind': str(job.get('kind', ''))[:80], 'axes': dimensions,
                   'captureCount': captures, 'blocksPerPoint': blocks, 'shotsPerBlock': shots,
                   'shotsPerPoint': per_point, 'totalScanPoints': expected}
        frequency = next((a for a in dimensions if a['name'] == 'readout_frequency_hz'), None)
        amplitude = next((a for a in dimensions if a['name'] == 'readout_amplitude'), None)
        if frequency:
            summary['frequencyPoints'] = frequency['points']
        if amplitude:
            summary['amplitudePoints'] = amplitude['points']
        summaries.append(summary)
        total_shots += expected * per_point
    if plan.get('estimated_total_shots') != total_shots:
        return None
    return {'jobs': summaries, 'totalScanPoints': sum(rows), 'estimatedTotalShots': total_shots,
            'pointMeaning': 'One completed native scan row; frequency-axis points and repeated blocks are distinct.'}


def registered_plan_geometry(db, root, request, result, remaining):
    """Return geometry only from this operation's registered, matching plan."""
    artifacts = result.get('artifacts', [])
    if not isinstance(artifacts, list) or len(artifacts) > 128:
        return {'status': 'unavailable'}
    candidates = [a for a in artifacts if isinstance(a, dict) and a.get('name') == 'plan.json']
    if len(candidates) != 1:
        return {'status': 'not_registered'}
    try:
        artifact = candidates[0]
        row = db.execute('SELECT path,sha256,payload FROM platform_artifacts WHERE id=?', (artifact.get('id'),)).fetchone()
        metadata = _object(row['payload']) if row else None
        if (not metadata or metadata.get('name') != 'plan.json' or metadata.get('id') != artifact.get('id')
                or metadata.get('sha256') != row['sha256'] or artifact.get('sha256') != row['sha256']):
            return {'status': 'unavailable'}
        root = checked_path(root)
        expected = root / 'data' / 'runs' / 'arclight_operations' / hashlib.sha256(request['operationId'].encode()).hexdigest() / 'plan.json'
        path = checked_path(row['path'], parent=root)
        if path != expected or path.is_symlink() or not path.is_file():
            return {'status': 'unavailable'}
        with path.open('rb') as stream:
            content = stream.read(min(MAX_PLAN_BYTES, remaining[0]) + 1)
        if len(content) > MAX_PLAN_BYTES or len(content) > remaining[0]:
            remaining[0] = 0
            return {'status': 'budget_omitted'}
        remaining[0] -= len(content)
        if hashlib.sha256(content).hexdigest() != row['sha256']:
            return {'status': 'unavailable'}
        plan = json.loads(content)
        if (not isinstance(plan, dict) or plan.get('adapter') != 'arclight' or plan.get('compileOnlyEvidence')
                or plan.get('requestIdentity') != {key: request.get(key) for key in IDENTITY}
                or any(plan.get(key) != request.get(key) for key in ('operationId', 'resourceId', 'procedureId'))
                or plan.get('parameters') != result.get('parameters')
                or not isinstance(plan.get('parameters'), dict)
                or any(plan['parameters'].get(k) != v for k, v in request.get('parameters', {}).items())):
            return {'status': 'unavailable'}
        geometry = _geometry(plan)
        return ({'status': 'registered_plan', 'planMetadataRead': True, 'artifactId': artifact['id'], 'sha256': row['sha256'],
                 'operationId': request['operationId'], **geometry} if geometry else {'status': 'unavailable'})
    except (APIError, OSError, ValueError, TypeError, KeyError, AttributeError):
        return {'status': 'unavailable'}


def acquisition_context(db, workflow, root, captured_at):
    """Explain persisted receipts, not recompute scientific quality or dispatch."""
    snapshot = {'schema': 'chat-acquisition-state/1', 'snapshotAt': captured_at,
                'snapshotAtBeijing': beijing_time(captured_at), 'displayTimeZone': 'Asia/Shanghai (UTC+08:00)',
                'workflowId': workflow['id'], 'workflowRevision': workflow['revision'],
                'eventSeq': db.execute('SELECT coalesce(max(seq),0) FROM events').fetchone()[0],
                'currentNodeId': workflow.get('currentNodeId'), 'readOnly': True, 'rawFileRead': False,
                'meaning': 'As-of this database snapshot, not live progress at answer delivery. No experiment was started by chat.',
                'nodes': []}
    remaining = [MAX_PLAN_READ_BYTES]
    ordered = sorted(workflow['nodes'], key=lambda n: n['id'] != workflow.get('currentNodeId'))
    for node in ordered[:64]:
        latest = node.get('result') or {}
        item = {'nodeId': node['id'], 'label': node.get('label'), 'resourceId': node['resourceId'],
                'procedureId': node['procedureId'], 'attemptId': node.get('latestAttemptId'),
                'engineState': node.get('engineState'), 'attemptCount': node.get('attempt', 0),
                'controllerQuality': latest.get('quality', {}).get('status'), 'stale': bool(latest.get('stale')),
                'verificationPending': latest.get('verificationPending') is True, 'operations': [],
                'committedParameters': _committed(db, workflow['id'], node['resourceId'])}
        snapshot['nodes'].append(item)
        if not item['attemptId']:
            item['acquisitionState'] = 'not_measured' if node.get('attempt', 0) == 0 else 'unavailable'
            continue
        attempt_fields = ('id', 'nodeId', 'number', 'operationId', 'verificationOperationId', 'parameterVersions', 'executionBinding')
        row = db.execute('SELECT ' + _projection('payload', attempt_fields) + ' AS payload FROM platform_attempts WHERE id=? AND workflow_id=? AND node_id=?',
                         (item['attemptId'], workflow['id'], node['id'])).fetchone()
        attempt = _object(row['payload']) if row else None
        if not attempt or attempt.get('id') != item['attemptId'] or attempt.get('nodeId') != node['id']:
            item['acquisitionState'] = 'metadata_inconsistent'
            continue
        result_fields = ('acquiredAt', 'dataKind', 'partial', 'parameters', 'candidateParameters')
        result_projection = (_projection('result', result_fields)[:-1] +
            ",'provenance'," + _projection('result', ('operationId', 'freshAcquisition', 'executionBinding'), '$.provenance') +
            ",'metrics'," + _projection('result', METRICS, '$.metrics') +
            ",'analysisQuality'," + _projection('result', ('accepted',), '$.analysisQuality') + ')')
        effects = db.execute('SELECT operation_id,state,request,' + result_projection + ' AS result FROM platform_effects WHERE workflow_id=? AND attempt_id=? ORDER BY operation_id LIMIT 8',
                             (workflow['id'], item['attemptId'])).fetchall()
        raw_by_stage, fresh_by_stage = {}, {}
        for effect in effects[:8]:
            request = _object(effect['request'])
            if (not request or request.get('operationId') != effect['operation_id']
                    or any(request.get(key) != value for key, value in {
                        'runId': workflow['id'], 'nodeId': node['id'], 'attemptId': item['attemptId'],
                        'resourceId': node['resourceId'], 'procedureId': node['procedureId'],
                        'parameterVersions': attempt.get('parameterVersions')}.items())
                    or not _execution_matches(workflow, node, attempt, request)):
                continue
            operation = effect['operation_id']
            if operation == attempt.get('operationId'):
                stage = 'selection'
            elif operation == (attempt.get('verificationOperationId') or item['attemptId'] + '/verify'):
                stage = 'verification'
            else:
                continue
            result = _object(effect['result']) or {}
            provenance = result.get('provenance') or {}
            fresh = (effect['state'] == 'completed' and result.get('dataKind') == 'real'
                     and not result.get('partial') and provenance.get('freshAcquisition') is True
                     and provenance.get('operationId') == operation
                     and (not attempt.get('executionBinding') or
                          provenance.get('executionBinding') == attempt['executionBinding']))
            raw = _registered_operation_raw(db, operation) if fresh else set()
            plans = db.execute("""SELECT a.value FROM platform_effects e JOIN json_each(CASE WHEN json_valid(e.result) THEN e.result ELSE '{}' END,'$.artifacts') a
                WHERE e.operation_id=? AND json_valid(e.result) AND json_valid(a.value)
                AND json_extract(a.value,'$.name')='plan.json' LIMIT 2""", (operation,)).fetchall()
            result['artifacts'] = [value for row in plans if (value := _object(row[0])) is not None]
            raw_by_stage[stage], fresh_by_stage[stage] = raw, fresh
            entry = {'stage': stage, 'operationId': operation, 'receiptState': effect['state'],
                     'freshCompletedReceipt': fresh, 'registeredRawSha256': sorted(raw)[:8],
                     'registeredRawCount': len(raw), 'registeredRawSetSha256': hashlib.sha256(
                         json.dumps(sorted(raw), separators=(',', ':')).encode()).hexdigest(),
                     'rawHashListTruncated': len(raw) > 8,
                     'acquisitionState': 'completed' if effect['state'] == 'completed' else effect['state'],
                     'acquiredAt': result.get('acquiredAt'), 'acquiredAtBeijing': beijing_time(result.get('acquiredAt')),
                     'acquiredAtMeaning': 'Operation acquisition completion timestamp; not the first hardware trigger.',
                     'dataKind': result.get('dataKind'),
                     'parameterEvidence': {'source': 'this_operation_result',
                         'operationId': operation, 'attemptId': item['attemptId'],
                         'measuredCandidates': parameter_facts(result.get('candidateParameters'), 'measured_result_candidate') if fresh and raw else {},
                         'configuredInputs': parameter_facts(result.get('parameters'), 'acquisition_configured_input'),
                         'meaning': 'Candidates are the measured/fitted outputs of this operation. Configured inputs are not measured outputs; committed ledger values are separate.'},
                     'metrics': {k: v for k, v in (result.get('metrics') or {}).items() if k in METRICS and number(v)},
                     'nativeAnalysisAccepted': (result.get('analysisQuality') or {}).get('accepted'),
                     'geometry': registered_plan_geometry(db, root, request, result, remaining),
                     'rawFileRead': False}
            telemetry = node.get('telemetry') or {}
            completed, total = telemetry.get('completedRows'), telemetry.get('totalRows')
            if (telemetry.get('controllerOperationId', telemetry.get('operationId')) == operation
                    and telemetry.get('controllerAttemptId', item['attemptId']) == item['attemptId']
                    and _integer(completed) and _integer(total, 1)
                    and completed <= total):
                entry['progress'] = {'completedScanPoints': completed, 'totalScanPoints': total,
                                     'basis': 'actual-completed-native-scan-points', 'asOfRevision': workflow['revision']}
                if effect['state'] == 'pending' and completed > 0:
                    entry['acquisitionState'] = 'acquiring'
            # analysisAccepted in telemetry can be left over from another stage.
            # Only persisted final node quality is a controller acceptance claim.
            item['operations'].append(entry)
        verified = (node.get('engineState') == 'verified' and item['controllerQuality'] == 'passed'
                    and not item['stale'] and not latest.get('partial'))
        paired = (all(fresh_by_stage.get(s) and raw_by_stage.get(s) for s in ('selection', 'verification'))
                  and raw_by_stage['selection'].isdisjoint(raw_by_stage['verification']))
        item['independentAcquisition'] = {
            'status': 'controller_accepted' if verified and paired else 'pending' if item['verificationPending'] else 'not_established',
            'sameAttemptAllowed': True, 'completedFreshReceiptsWithDisjointRegisteredRaw': bool(paired),
            'basis': 'Controller acceptance plus registered receipt metadata; this chat does not rehash raw acquisition files.'}
    return snapshot


def bound_chat_observations(payload):
    """Keep core state intact. Drop optional artifact summaries, oldest priority last."""
    context = payload['context']
    observations = context.get('observations', [])
    summary = context.get('observationSummary', {})
    while observations and javascript_json_units_upper_bound(payload) > MAX_CHAT_UNITS:
        observations.pop()
        summary['omitted'] = summary.get('omitted', 0) + 1
        summary['provided'] = len(observations)
    if javascript_json_units_upper_bound(payload) > MAX_CHAT_UNITS:
        raise APIError('CHAT_CONTEXT_TOO_LARGE', '聊天核心状态超过输入上限，未截断当前实验状态或调用模型', 413)
    return payload
