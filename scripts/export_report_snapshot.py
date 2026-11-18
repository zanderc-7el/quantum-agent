"""Freeze one or more reports in one read-only, consistent SQLite transaction.

No platform imports: constructing Controller/Store would initialize live state.
The generated JSON is the sole input to export_report_pptx.mjs. Image paths
must be in --artifact-root and match the registered hash before export.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3


PROTECTED = Path('D:/qoskit-workspace')


def checked(path):
    value = Path(path).absolute()
    # Reject the named protected tree before resolving links or opening it.
    if value == PROTECTED or PROTECTED in value.parents:
        raise ValueError('Protected workspace is outside report scope')
    value = value.resolve()
    if value == PROTECTED or PROTECTED in value.parents:
        raise ValueError('Protected workspace is outside report scope')
    return value


def result_artifacts(result):
    if not isinstance(result, dict):
        return
    yield from result.get('artifacts', [])
    if isinstance(result.get('selectionResult'), dict):
        yield from result['selectionResult'].get('artifacts', [])


def _read_report(connection, workflow_id, artifact_root, generated_at):
    row = connection.execute('SELECT payload FROM platform_workflows WHERE id=?', (workflow_id,)).fetchone()
    if row is None:
        raise ValueError('Workflow not found: ' + workflow_id)
    workflow = json.loads(row[0])
    if workflow.get('id') != workflow_id:
        raise ValueError('Workflow identity mismatch: ' + workflow_id)
    nodes, artifacts = [], {}
    for node in workflow['nodes']:
        attempts = [json.loads(r[0]) for r in connection.execute(
            'SELECT payload FROM platform_attempts WHERE workflow_id=? AND node_id=? ORDER BY number',
            (workflow_id, node['id']))]
        decisions = [json.loads(r[0]) for r in connection.execute(
            'SELECT payload FROM platform_decisions WHERE workflow_id=? AND node_id=? ORDER BY rowid',
            (workflow_id, node['id']))]
        nodes.append({'node': node, 'attempts': attempts, 'decisions': decisions})
        for attempt in attempts:
            for artifact in result_artifacts(attempt.get('result')):
                record = connection.execute('SELECT path,sha256,payload FROM platform_artifacts WHERE id=?',
                                            (artifact['id'],)).fetchone()
                if record is None:
                    raise ValueError('Unregistered artifact: ' + artifact['id'])
                if record[1] != artifact['sha256']:
                    raise ValueError('Result and registry hashes differ: ' + artifact['id'])
                if json.loads(record[2]).get('kind') != artifact.get('kind'):
                    raise ValueError('Result and registry types differ: ' + artifact['id'])
                path = checked(record[0])
                if not path.is_relative_to(artifact_root):
                    raise ValueError('Artifact is outside explicit root: ' + artifact['id'])
                artifacts[artifact['id']] = {**json.loads(record[2]), 'id': artifact['id'],
                    'path': str(path), 'sha256': record[1]}
    return {'schema': 'qcal-report/1', 'generatedAt': generated_at,
        'source': 'recorded_data_only', 'workflow': workflow, 'nodes': nodes,
        'workflowComplete': workflow.get('workflowComplete') is True,
        'artifactRoot': str(artifact_root), 'artifactIndex': artifacts,
        'snapshotSemantics': 'Consistent read-only SQLite snapshot. Ongoing attempts remain incomplete. Image bytes verified against registered SHA256.'}


def _verify_images(artifacts):
    for artifact in artifacts.values():
        if artifact.get('kind') == 'image':
            actual = hashlib.sha256(Path(artifact['path']).read_bytes()).hexdigest()
            if actual != artifact['sha256']:
                raise ValueError('Image integrity mismatch: ' + artifact['id'])


def snapshot_many(db, workflow_ids, artifact_root):
    if not workflow_ids or any(not isinstance(value, str) or not value.strip() for value in workflow_ids):
        raise ValueError('At least one explicit workflow ID is required')
    if len(set(workflow_ids)) != len(workflow_ids):
        raise ValueError('Duplicate workflow IDs are not allowed')
    db, artifact_root = checked(db), checked(artifact_root)
    generated_at = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)) as connection:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        reports = [_read_report(connection, workflow_id, artifact_root, generated_at) for workflow_id in workflow_ids]
        connection.rollback()
    # Hash files only after releasing the consistent, read-only database view.
    for report in reports:
        _verify_images(report['artifactIndex'])
    if len(reports) == 1:
        return reports[0]
    return {'schema': 'qcal-report-bundle/1', 'generatedAt': generated_at, 'source': 'recorded_data_only',
        'reports': reports,
        'snapshotSemantics': 'All tasks frozen in one read-only SQLite transaction. Each task retains its own profile, attempts and acceptance; no cross-task calibration reuse or merged pass status.'}


def snapshot(db, workflow_id, artifact_root):
    return snapshot_many(db, [workflow_id], artifact_root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--workflow', required=True)
    parser.add_argument('--additional-workflow', action='append', default=[], help='Additional explicit task ID; repeat for more tasks')
    parser.add_argument('--artifact-root', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = checked(args.output)
    if output.exists():
        raise ValueError('Use a new snapshot filename; existing snapshots are immutable')
    report = snapshot_many(args.db, [args.workflow, *args.additional_workflow], args.artifact_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    reports = report.get('reports', [report])
    summary = {'snapshot': str(output)}
    if len(reports) == 1:
        summary.update(nodes=len(report['nodes']), workflowComplete=report['workflowComplete'],
                       artifacts=len(report['artifactIndex']))
    else:
        summary['workflows'] = [{'id': item['workflow']['id'], 'nodes': len(item['nodes']),
            'workflowComplete': item['workflowComplete'], 'artifacts': len(item['artifactIndex'])}
            for item in reports]
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
