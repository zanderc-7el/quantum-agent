"""Immutable local result archive and an unconfigured cloud outbox.

This is an index in the controller's existing database, not another experiment
authority. No network client or background uploader is installed by this module.
Large files remain in the artifact registry and are addressed by ID and hash.
"""
from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from .common import APIError, checked_path, digest, dumps, file_hash, now

SCHEMA_VERSION = 1


def ensure_schema(db):
    # The caller owns the transaction, including the migration and backfill.
    for statement in (
        'CREATE TABLE IF NOT EXISTS platform_accounting_workflows(workflow_id TEXT PRIMARY KEY,parent_id TEXT)',
        'CREATE INDEX IF NOT EXISTS platform_accounting_parent ON platform_accounting_workflows(parent_id)',
        '''CREATE TABLE IF NOT EXISTS platform_shot_usage(
            workflow_id TEXT NOT NULL,operation_id TEXT PRIMARY KEY,reserved INTEGER NOT NULL CHECK(reserved>0),
            confirmed INTEGER,resolution TEXT NOT NULL DEFAULT 'unconfirmed')''',
        'CREATE INDEX IF NOT EXISTS platform_shot_usage_workflow ON platform_shot_usage(workflow_id)',
        'CREATE TABLE IF NOT EXISTS platform_archive_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)',
        '''CREATE TABLE IF NOT EXISTS platform_result_archive(
            seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
            source_key TEXT NOT NULL, content_hash TEXT NOT NULL, kind TEXT NOT NULL,
            workflow_id TEXT NOT NULL, node_id TEXT, mode TEXT NOT NULL,
            created_at TEXT NOT NULL, payload TEXT NOT NULL,
            UNIQUE(source_key,content_hash))''',
        'CREATE INDEX IF NOT EXISTS platform_archive_workflow ON platform_result_archive(workflow_id,seq DESC)',
        '''CREATE TABLE IF NOT EXISTS platform_archive_outbox(
            archive_id TEXT PRIMARY KEY REFERENCES platform_result_archive(id),
            content_hash TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'waiting_configuration',
            created_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT, remote_receipt TEXT)''',
    ):
        db.execute(statement)
    version = db.execute('SELECT coalesce(max(version),0) FROM platform_archive_migrations').fetchone()[0]
    if version > SCHEMA_VERSION:
        raise APIError('ARCHIVE_SCHEMA_NEWER', '本地数据库版本较新，请使用对应版本的软件', 409)
    return version < SCHEMA_VERSION


def _workflow(db, workflow_id):
    row = db.execute('SELECT payload FROM platform_workflows WHERE id=?', (workflow_id,)).fetchone()
    return json.loads(row[0]) if row else None


def _artifacts(db, result, root):
    """Only already registered references; no raw-file reads during acquisition."""
    found, seen = [], set()
    for artifact in result.get('artifacts', []) + result.get('partialArtifacts', []):
        key = (artifact.get('id'), artifact.get('sha256'))
        if key in seen:
            continue
        seen.add(key)
        row = db.execute('SELECT path,sha256,payload FROM platform_artifacts WHERE id=?', (key[0],)).fetchone()
        entry = {k: artifact.get(k) for k in ('id', 'name', 'kind', 'sha256')}
        entry['registered'] = bool(row and row[1] == key[1])
        if entry['registered']:
            entry['url'] = '/api/artifacts/' + artifact['id']
            # Avoid resolving, opening or uploading external source paths.
            try:
                entry['relativePath'] = Path(row[0]).relative_to(root).as_posix()
                entry['storage'] = 'project_file'
            except ValueError:
                entry['storage'] = 'external_registered_reference'
        found.append(entry)
    return found


def _base(workflow, node_id):
    node = next((n for n in workflow.get('nodes', []) if n['id'] == node_id), {})
    return {'workflowId': workflow['id'], 'workflowName': workflow.get('name'),
            'revision': workflow.get('revision'), 'nodeId': node_id,
            'nodeLabel': node.get('label'), 'resourceId': node.get('resourceId'),
            'procedureId': node.get('procedureId'), 'mode': workflow.get('mode', 'unknown')}


def _insert(db, source_key, payload):
    content_hash = digest(payload)
    archive_id = 'archive-' + digest({'source': source_key, 'content': content_hash})
    timestamp = now()
    item = {**payload, 'id': archive_id, 'createdAt': timestamp, 'contentSha256': content_hash}
    db.execute('''INSERT OR IGNORE INTO platform_result_archive
        (id,source_key,content_hash,kind,workflow_id,node_id,mode,created_at,payload)
        VALUES(?,?,?,?,?,?,?,?,?)''', (archive_id, source_key, content_hash, payload['kind'],
        payload['workflowId'], payload.get('nodeId'), payload['mode'], timestamp, dumps(item)))
    db.execute('''INSERT OR IGNORE INTO platform_archive_outbox
        (archive_id,content_hash,state,created_at) VALUES(?,?,'waiting_configuration',?)''',
        (archive_id, content_hash, timestamp))
    return archive_id


def record_effect(db, operation_id, root):
    from .shot_accounting import capture
    capture(db, operation_id, root)
    row = db.execute('SELECT * FROM platform_effects WHERE operation_id=?', (operation_id,)).fetchone()
    if not row or row['state'] not in ('completed', 'cancelled', 'failed_after_shutdown') or not row['result']:
        return None
    request, result = json.loads(row['request']), json.loads(row['result'])
    wf = _workflow(db, row['workflow_id'])
    if wf is None:
        return None
    artifacts = _artifacts(db, result, root)
    payload = {**_base(wf, request.get('nodeId')), 'kind': 'acquisition',
        'operationId': operation_id, 'attemptId': row['attempt_id'],
        'role': 'verification' if operation_id.endswith('/verify') else 'selection',
        'receiptState': row['state'], 'dataKind': result.get('dataKind'),
        'acquiredAt': result.get('acquiredAt'), 'parameters': result.get('parameters') or request.get('parameters', {}),
        'parameterVersions': request.get('parameterVersions', {}), 'metrics': result.get('metrics', {}),
        'qualityStatus': 'unverified',
        'quality': {'status': 'unverified', 'reasons': ['采集回执已保存，验收结果见对应尝试记录']},
        'artifacts': artifacts, 'artifactCount': len(artifacts),
        'details': {'receiptQuality': result.get('quality'), 'provenance': result.get('provenance'),
                    'partial': row['state'] != 'completed' or bool(result.get('partial'))},
        'receiptSha256': digest(result), 'requestSha256': digest(request)}
    # Workflow progress changes cannot turn an identical acquisition into a new record.
    payload.pop('revision', None)
    return _insert(db, 'operation:' + operation_id, payload)


def record_attempt(db, workflow_id, attempt, root):
    result = attempt.get('result')
    if not isinstance(result, dict) or not attempt.get('endedAt'):
        return None
    wf = _workflow(db, workflow_id)
    if not wf:
        return None
    artifacts = _artifacts(db, result, root)
    for entry in _artifacts(db, result.get('selectionResult') or {}, root):
        if entry not in artifacts:
            artifacts.append(entry)
    quality = deepcopy(result.get('quality') or {'status': 'unverified', 'reasons': []})
    if attempt.get('mode', wf.get('mode')) == 'replay':
        quality = {'status': 'historical', 'reasons': ['历史回放，不计为本轮独立验收']}
    payload = {**_base(wf, attempt['nodeId']), 'kind': 'attempt',
        'attemptId': attempt['id'], 'attemptNumber': attempt.get('number'),
        'operationId': attempt.get('operationId'), 'verificationOperationId': attempt.get('verificationOperationId'),
        'role': 'historical' if wf.get('mode') == 'replay' else 'assessment',
        'attemptState': attempt.get('state'), 'dataKind': result.get('dataKind'),
        'acquiredAt': result.get('acquiredAt'), 'endedAt': attempt.get('endedAt'),
        'parameters': attempt.get('parameters', {}), 'parameterVersions': attempt.get('parameterVersions', {}),
        'metrics': result.get('metrics', {}), 'quality': quality, 'qualityStatus': quality['status'],
        'artifacts': artifacts, 'artifactCount': len(artifacts),
        'details': {'partial': result.get('partial', False), 'verificationPending': result.get('verificationPending', False),
                    'diagnosticScope': result.get('diagnosticScope'), 'supplementalQuality': result.get('supplementalQuality'),
                    'gatesVersion': wf.get('gatesVersion'), 'sourceRecordId': attempt.get('sourceRecordId')},
        'resultSha256': digest(result)}
    payload.pop('revision', None)
    return _insert(db, 'attempt:' + attempt['id'], payload)


def backfill(db, root):
    """Index existing ledgers without touching workflow revisions or old files."""
    before = db.execute('SELECT count(*) FROM platform_result_archive').fetchone()[0]
    for row in db.execute("SELECT operation_id FROM platform_effects WHERE state IN ('completed','cancelled','failed_after_shutdown')").fetchall():
        record_effect(db, row[0], root)
    for row in db.execute('SELECT workflow_id,payload FROM platform_attempts').fetchall():
        record_attempt(db, row[0], json.loads(row[1]), root)
    return db.execute('SELECT count(*) FROM platform_result_archive').fetchone()[0] - before


class ResultArchive:
    def __init__(self, store, root):
        self.store, self.root = store, Path(root)

    def status(self):
        with self.store.connect() as rt:
            counts = dict(rt.db.execute('SELECT kind,count(*) FROM platform_result_archive GROUP BY kind'))
            latest = rt.db.execute('SELECT max(created_at) FROM platform_result_archive').fetchone()[0]
            pending = rt.db.execute("SELECT count(*) FROM platform_archive_outbox WHERE state='waiting_configuration'").fetchone()[0]
        return {'schemaVersion': SCHEMA_VERSION, 'storage': 'sqlite', 'location': 'data/platform.sqlite',
            'local': {'recordCount': sum(counts.values()), 'acquisitionCount': counts.get('acquisition', 0),
                      'attemptCount': counts.get('attempt', 0), 'reportCount': counts.get('report', 0), 'latestArchivedAt': latest},
            'cloud': {'configured': False, 'state': 'not_configured', 'pendingCount': pending, 'syncedCount': 0,
                      'message': '已保存到本机，云端尚未配置'}}

    def list(self, workflow_id=None, limit=30, cursor=None):
        try:
            limit = int(limit)
            cursor = int(cursor) if cursor not in (None, '') else None
        except (TypeError, ValueError):
            raise APIError('INVALID_ARCHIVE_QUERY', '归档分页参数无效') from None
        if not 1 <= limit <= 100 or (cursor is not None and cursor < 1):
            raise APIError('INVALID_ARCHIVE_QUERY', '归档分页数量须为1–100，游标须为正整数')
        clauses, values = [], []
        if workflow_id:
            clauses.append('workflow_id=?'); values.append(workflow_id)
        if cursor is not None:
            clauses.append('seq<?'); values.append(cursor)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with self.store.connect() as rt:
            rows = rt.db.execute('SELECT seq,payload FROM platform_result_archive' + where + ' ORDER BY seq DESC LIMIT ?', (*values, limit + 1)).fetchall()
        keys = ('id', 'kind', 'workflowId', 'workflowName', 'nodeId', 'nodeLabel', 'resourceId', 'procedureId', 'mode',
                'qualityStatus', 'role', 'createdAt', 'acquiredAt', 'artifactCount', 'revision', 'attemptNumber')
        items = [{k: item.get(k) for k in keys} for item in (json.loads(row['payload']) for row in rows[:limit])]
        return {'items': items, 'nextCursor': str(rows[limit - 1]['seq']) if len(rows) > limit else None}

    def item(self, archive_id):
        with self.store.connect() as rt:
            row = rt.db.execute('SELECT payload FROM platform_result_archive WHERE id=?', (archive_id,)).fetchone()
            if row is None:
                raise APIError('NOT_FOUND', '归档记录不存在', 404)
            item = json.loads(row[0])
            wf = _workflow(rt.db, item['workflowId']) or {}
            node = next((n for n in wf.get('nodes', []) if n['id'] == item.get('nodeId')), {})
            item['currentContext'] = {'workflowRevision': wf.get('revision'),
                'nodeState': node.get('engineState'), 'latestAttemptId': node.get('latestAttemptId'),
                'isLatestAttempt': bool(item.get('attemptId') and item['attemptId'] == node.get('latestAttemptId')),
                'meaning': '归档验收状态记录当时结论，当前有效性以蓝图为准'}
            return item

    def pending_sync(self, limit=100):
        """Future uploader input; intentionally no transport, acknowledgement or credentials."""
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError('limit must be 1..100')
        with self.store.connect() as rt:
            return [dict(row) for row in rt.db.execute('''SELECT archive_id,content_hash,state FROM platform_archive_outbox
                WHERE state='waiting_configuration' ORDER BY rowid LIMIT ?''', (limit,))]


def register_report_archive(db_path, workflow_id, export_id, path, sha256, snapshot_sha256, revision,
                            *, root, kind='pptx', audit=None):
    root = checked_path(root)
    db_path = checked_path(db_path, parent=root)
    path = checked_path(path, parent=root)
    if not path.is_file() or file_hash(path) != sha256:
        raise APIError('ARTIFACT_HASH_MISMATCH', '报告文件与生成回执不一致')
    if kind not in ('pptx', 'html', 'text') or not isinstance(revision, int) or revision < 0:
        raise APIError('INVALID_REPORT_ARCHIVE', '报告格式或快照版本无效')
    with closing(sqlite3.connect(str(db_path), timeout=30, isolation_level=None)) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN IMMEDIATE')
        try:
            wf = _workflow(db, workflow_id)
            if wf is None:
                raise APIError('NOT_FOUND', '任务不存在', 404)
            artifact_id = 'report-' + digest({'exportId': export_id, 'sha256': sha256})
            artifact = {'id': artifact_id, 'name': path.name, 'kind': kind, 'sha256': sha256,
                        'url': '/api/artifacts/' + artifact_id}
            db.execute('INSERT OR IGNORE INTO platform_artifacts VALUES(?,?,?,?)', (artifact_id, str(path), sha256, dumps(artifact)))
            payload = {**_base(wf, None), 'revision': revision, 'kind': 'report', 'role': 'report',
                'exportId': export_id, 'format': kind, 'snapshotSha256': snapshot_sha256,
                'qualityStatus': 'not_applicable', 'quality': {'status': 'not_applicable', 'reasons': ['报告生成不改变实验验收状态']},
                'artifacts': [{**artifact, 'relativePath': path.relative_to(root).as_posix(), 'storage': 'project_file', 'registered': True}],
                'artifactCount': 1, 'details': {'audit': audit or {}}, 'parameters': {}, 'parameterVersions': {}, 'metrics': {}}
            result = _insert(db, 'report:' + export_id, payload)
            db.execute('COMMIT')
            return result
        except Exception:
            db.execute('ROLLBACK')
            raise
