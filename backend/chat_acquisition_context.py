"""Read-only, bounded acquisition status for chat; never an acceptance gate.

Only registered plan JSON is opened. Raw acquisition files are never read.
All database rows must come from the caller's one pinned SQLite read snapshot.
"""
from contextlib import contextmanager
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
                'workflowId': workflow['id'], 'workflowRevision': workflow['revision'],
                'eventSeq': db.execute('SELECT coalesce(max(seq),0) FROM events').fetchone()[0],
                'currentNodeId': workflow.get('currentNodeId'), 'readOnly': True, 'rawFileRead': False,
                'meaning': 'As-of this database snapshot, not live progress at answer delivery. No experiment was started by chat.',
                'nodes': []}
    remaining = [MAX_PLAN_READ_BYTES]
    ordered = sorted(workflow['nodes'], key=lambda n: n['id'] != workflow.get('currentNodeId'))
    for node in ordered[:64]:
        latest = node.get('result') or {}
        item = {'nodeId': node['id'], 'procedureId': node['procedureId'], 'attemptId': node.get('latestAttemptId'),
                'engineState': node.get('engineState'), 'attemptCount': node.get('attempt', 0),
                'controllerQuality': latest.get('quality', {}).get('status'), 'stale': bool(latest.get('stale')),
                'verificationPending': latest.get('verificationPending') is True, 'operations': []}
        snapshot['nodes'].append(item)
        if not item['attemptId']:
            item['acquisitionState'] = 'not_measured' if node.get('attempt', 0) == 0 else 'unavailable'
            continue
        row = db.execute('SELECT payload FROM platform_attempts WHERE id=? AND workflow_id=? AND node_id=?',
                         (item['attemptId'], workflow['id'], node['id'])).fetchone()
        attempt = _object(row['payload']) if row else None
        if not attempt or attempt.get('id') != item['attemptId'] or attempt.get('nodeId') != node['id']:
            item['acquisitionState'] = 'metadata_inconsistent'
            continue
        effects = db.execute('SELECT operation_id,state,request,result FROM platform_effects WHERE workflow_id=? AND attempt_id=?',
                             (workflow['id'], item['attemptId'])).fetchall()
        raw_by_stage, fresh_by_stage = {}, {}
        for effect in effects[:8]:
            request = _object(effect['request'])
            if (not request or request.get('operationId') != effect['operation_id']
                    or any(request.get(key) != value for key, value in {
                        'runId': workflow['id'], 'nodeId': node['id'], 'attemptId': item['attemptId'],
                        'resourceId': node['resourceId'], 'procedureId': node['procedureId'],
                        'adapterId': workflow.get('adapterId'), 'adapterProfileHash': workflow.get('adapterProfileHash'),
                        'parameterVersions': attempt.get('parameterVersions')}.items())):
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
                     and provenance.get('operationId') == operation)
            raw = _registered_raw(db, result) if fresh else set()
            raw_by_stage[stage], fresh_by_stage[stage] = raw, fresh
            entry = {'stage': stage, 'operationId': operation, 'receiptState': effect['state'],
                     'freshCompletedReceipt': fresh, 'registeredRawSha256': sorted(raw),
                     'acquisitionState': 'completed' if effect['state'] == 'completed' else effect['state'],
                     'acquiredAt': result.get('acquiredAt'), 'dataKind': result.get('dataKind'),
                     'metrics': {k: v for k, v in result.get('metrics', {}).items() if k in METRICS and number(v)},
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
